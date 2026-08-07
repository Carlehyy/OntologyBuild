#!/usr/bin/env node

import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';

const scriptPath = fileURLToPath(import.meta.url);
const repositoryRoot = path.resolve(path.dirname(scriptPath), '../..');
const strictArchive = process.argv.includes('--strict-archive');
const selfTest = process.argv.includes('--self-test');
const knownArguments = new Set(['--strict-archive', '--self-test', '--help']);
const ignoredDirectoryNames = new Set([
  '.artifacts',
  '.git',
  '.mypy_cache',
  '.pytest_cache',
  '.ruff_cache',
  '.venv',
  'artifacts',
  'build',
  'coverage',
  'dist',
  'node_modules',
  'test-results',
  'venv',
]);
const requiredDocumentationFiles = [
  'docs/README.md',
  'docs/development/README.md',
  'docs/operations/README.md',
];
const documentationIndexRules = [];
const rootDocumentationIndexes = [
  'docs/development/README.md',
  'docs/operations/README.md',
];

if (process.argv.includes('--help')) {
  process.stdout.write(
    [
      'Usage: node scripts/ci/check-markdown-links.mjs [options]',
      '',
      'Options:',
      '  --strict-archive  Treat problems originating in docs/archive as errors.',
      '  --self-test       Run built-in parser and GitHub-anchor regression tests.',
      '  --help            Show this help.',
      '',
    ].join('\n'),
  );
  process.exit(0);
}

const unknownArguments = process.argv.slice(2).filter((argument) => !knownArguments.has(argument));
if (unknownArguments.length > 0) {
  process.stderr.write(`Unknown argument(s): ${unknownArguments.join(', ')}\n`);
  process.exit(2);
}

function normalizeRepositoryPath(absolutePath) {
  return path.relative(repositoryRoot, absolutePath).split(path.sep).join('/');
}

function isArchivePath(repositoryPath) {
  return repositoryPath === 'docs/archive' || repositoryPath.startsWith('docs/archive/');
}

function replaceNonNewlines(value) {
  return value.replace(/[^\r\n]/g, ' ');
}

function stripHtmlComments(markdown) {
  return markdown.replace(/<!--[\s\S]*?-->/g, replaceNonNewlines);
}

function stripInlineCode(line) {
  const characters = [...line];
  let cursor = 0;

  while (cursor < characters.length) {
    if (characters[cursor] !== '`') {
      cursor += 1;
      continue;
    }

    let delimiterLength = 1;
    while (characters[cursor + delimiterLength] === '`') {
      delimiterLength += 1;
    }

    let closing = cursor + delimiterLength;
    let found = false;
    while (closing < characters.length) {
      if (characters[closing] !== '`') {
        closing += 1;
        continue;
      }

      let closingLength = 1;
      while (characters[closing + closingLength] === '`') {
        closingLength += 1;
      }
      if (closingLength === delimiterLength) {
        found = true;
        break;
      }
      closing += closingLength;
    }

    if (!found) {
      cursor += delimiterLength;
      continue;
    }

    for (let index = cursor; index < closing + delimiterLength; index += 1) {
      characters[index] = ' ';
    }
    cursor = closing + delimiterLength;
  }

  return characters.join('');
}

function markdownLines(markdown) {
  const uncommented = stripHtmlComments(markdown);
  const rawLines = uncommented.split(/\r?\n/);
  const structuralLines = [];
  const linkLines = [];
  let fence = null;

  for (const rawLine of rawLines) {
    const fenceMatch = rawLine.match(/^\s{0,3}(?:>\s*)?(`{3,}|~{3,})/);
    if (fenceMatch) {
      const marker = fenceMatch[1][0];
      const length = fenceMatch[1].length;
      if (fence === null) {
        fence = { marker, length };
      } else if (fence.marker === marker && length >= fence.length) {
        fence = null;
      }
      structuralLines.push('');
      linkLines.push('');
      continue;
    }

    if (fence !== null) {
      structuralLines.push('');
      linkLines.push('');
      continue;
    }

    const isIndentedCode =
      /^(?: {4}|\t)/.test(rawLine)
      && !/^(?: {4}|\t)(?:[-+*]|\d+[.)])\s/.test(rawLine);
    if (isIndentedCode) {
      structuralLines.push('');
      linkLines.push('');
      continue;
    }

    structuralLines.push(rawLine);
    linkLines.push(stripInlineCode(rawLine));
  }

  return { structuralLines, linkLines };
}

function decodeHtmlEntities(value) {
  const named = new Map([
    ['amp', '&'],
    ['apos', "'"],
    ['gt', '>'],
    ['lt', '<'],
    ['quot', '"'],
  ]);

  return value.replace(/&(#x[0-9a-f]+|#\d+|[a-z]+);/gi, (entity, body) => {
    try {
      if (body.startsWith('#x') || body.startsWith('#X')) {
        return String.fromCodePoint(Number.parseInt(body.slice(2), 16));
      }
      if (body.startsWith('#')) {
        return String.fromCodePoint(Number.parseInt(body.slice(1), 10));
      }
    } catch {
      return entity;
    }
    return named.get(body.toLowerCase()) ?? entity;
  });
}

function headingText(value) {
  return decodeHtmlEntities(value)
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/<[^>]+>/g, '')
    .replace(/`+([^`]*)`+/g, '$1')
    .replace(/(^|[\s(])_([^_]+)_($|[\s)])/g, '$1$2$3')
    .replace(/[*~]/g, '')
    .trim();
}

function githubAnchor(value) {
  return headingText(value)
    .toLocaleLowerCase('en-US')
    .replace(/[\p{P}\p{S}]/gu, (character) => (character === '-' || character === '_' ? character : ''))
    .replace(/\s/g, '-');
}

function collectAnchors(structuralLines) {
  const anchors = new Set();
  const duplicateCounts = new Map();

  const addHeading = (text) => {
    const base = githubAnchor(text);
    const duplicateCount = duplicateCounts.get(base) ?? 0;
    duplicateCounts.set(base, duplicateCount + 1);
    anchors.add(duplicateCount === 0 ? base : `${base}-${duplicateCount}`);
  };

  for (let index = 0; index < structuralLines.length; index += 1) {
    const line = structuralLines[index];
    const atxMatch = line.match(/^\s{0,3}#{1,6}(?:\s+|$)(.*?)(?:\s+#+\s*)?$/);
    if (atxMatch) {
      addHeading(atxMatch[1]);
    } else if (
      index > 0
      && line.match(/^\s{0,3}(?:=+|-+)\s*$/)
      && structuralLines[index - 1].trim() !== ''
    ) {
      addHeading(structuralLines[index - 1].trim());
    }

    for (const match of line.matchAll(/<(?:a|[a-z][\w:-]*)\b[^>]*\b(?:id|name)\s*=\s*["']([^"']+)["'][^>]*>/gi)) {
      anchors.add(decodeHtmlEntities(match[1]));
    }
  }

  return anchors;
}

function parseInlineLinks(line, lineNumber) {
  const links = [];
  let cursor = 0;

  while (cursor < line.length) {
    const marker = line.indexOf('](', cursor);
    if (marker === -1) {
      break;
    }
    if (line.lastIndexOf('[', marker) === -1) {
      cursor = marker + 2;
      continue;
    }

    let destinationStart = marker + 2;
    while (/\s/.test(line[destinationStart] ?? '')) {
      destinationStart += 1;
    }

    if (line[destinationStart] === '<') {
      const closingAngle = line.indexOf('>', destinationStart + 1);
      if (closingAngle !== -1) {
        links.push({
          target: line.slice(destinationStart + 1, closingAngle),
          line: lineNumber,
          kind: 'inline',
        });
        cursor = closingAngle + 1;
        continue;
      }
      cursor = destinationStart + 1;
      continue;
    }

    let destinationEnd = destinationStart;
    let nestedParentheses = 0;
    let escaped = false;
    while (destinationEnd < line.length) {
      const character = line[destinationEnd];
      if (escaped) {
        escaped = false;
        destinationEnd += 1;
        continue;
      }
      if (character === '\\') {
        escaped = true;
        destinationEnd += 1;
        continue;
      }
      if (character === '(') {
        nestedParentheses += 1;
        destinationEnd += 1;
        continue;
      }
      if (character === ')') {
        if (nestedParentheses === 0) {
          break;
        }
        nestedParentheses -= 1;
        destinationEnd += 1;
        continue;
      }
      if (/\s/.test(character) && nestedParentheses === 0) {
        break;
      }
      destinationEnd += 1;
    }

    links.push({
      target: line.slice(destinationStart, destinationEnd),
      line: lineNumber,
      kind: 'inline',
    });
    cursor = Math.max(destinationEnd + 1, marker + 2);
  }

  return links;
}

function normalizeReferenceLabel(value) {
  return value.trim().replace(/\s+/g, ' ').toLocaleLowerCase('en-US');
}

function collectLinks(linkLines) {
  const links = [];
  const references = new Map();
  const definitionLines = new Set();

  for (let index = 0; index < linkLines.length; index += 1) {
    const line = linkLines[index];
    const definition = line.match(
      /^\s{0,3}\[([^\]]+)\]:\s*(?:<([^>]+)>|(\S+?))(?:\s+(?:"[^"]*"|'[^']*'|\([^)]*\)))?\s*$/,
    );
    if (!definition || definition[1].startsWith('^')) {
      continue;
    }
    const label = normalizeReferenceLabel(definition[1]);
    const target = definition[2] ?? definition[3];
    references.set(label, { target, line: index + 1 });
    definitionLines.add(index);
    links.push({ target, line: index + 1, kind: 'reference-definition' });
  }

  for (let index = 0; index < linkLines.length; index += 1) {
    if (definitionLines.has(index)) {
      continue;
    }
    const line = linkLines[index];
    links.push(...parseInlineLinks(line, index + 1));

    for (const match of line.matchAll(/(?<!!)\[([^\]\n]+)\]\[([^\]\n]*)\]/g)) {
      if (match[1].startsWith('^')) {
        continue;
      }
      const label = normalizeReferenceLabel(match[2] || match[1]);
      const reference = references.get(label);
      if (reference) {
        continue;
      }
      links.push({
        target: null,
        line: index + 1,
        kind: 'undefined-reference',
        label,
      });
    }

    for (const match of line.matchAll(/<[a-z][\w:-]*\b[^>]*\b(?:href|src)\s*=\s*["']([^"']+)["'][^>]*>/gi)) {
      links.push({ target: decodeHtmlEntities(match[1]), line: index + 1, kind: 'html' });
    }
  }

  return links;
}

function discoverMarkdownFiles(startDirectory) {
  const files = [];
  const visit = (directory) => {
    const entries = fs.readdirSync(directory, { withFileTypes: true })
      .sort((left, right) => left.name.localeCompare(right.name));
    for (const entry of entries) {
      if (entry.isSymbolicLink()) {
        continue;
      }
      const absolutePath = path.join(directory, entry.name);
      if (entry.isDirectory()) {
        const repositoryPath = normalizeRepositoryPath(absolutePath);
        // Claude/Codex local worktree snapshots are excluded from Git and may
        // contain documentation from unrelated branches. They are not part of
        // the repository being validated.
        const isLocalAgentWorktreeRoot = repositoryPath === '.claude/worktrees';
        if (!ignoredDirectoryNames.has(entry.name) && !isLocalAgentWorktreeRoot) {
          visit(absolutePath);
        }
      } else if (entry.isFile() && entry.name.toLowerCase().endsWith('.md')) {
        files.push(absolutePath);
      }
    }
  };
  visit(startDirectory);
  return files;
}

function decodeLinkComponent(value) {
  try {
    return { value: decodeURIComponent(value), error: null };
  } catch (error) {
    return { value, error };
  }
}

function classifyLinkTarget(rawTarget) {
  const target = rawTarget.trim();
  if (target === '') {
    return { type: 'local', pathname: '', fragment: '' };
  }
  if (target.startsWith('//')) {
    try {
      const remote = new URL(`https:${target}`);
      if (!remote.hostname) {
        throw new Error('missing hostname');
      }
      return { type: 'remote' };
    } catch (error) {
      return { type: 'invalid-remote', reason: error.message };
    }
  }

  const scheme = target.match(/^([a-z][a-z0-9+.-]*):/i);
  if (scheme) {
    const protocol = scheme[1].toLowerCase();
    if (!['http', 'https', 'mailto', 'tel'].includes(protocol)) {
      return { type: 'forbidden-scheme', protocol };
    }
    try {
      const remote = new URL(target);
      if (['http', 'https'].includes(protocol) && !remote.hostname) {
        throw new Error('missing hostname');
      }
      return { type: 'remote' };
    } catch (error) {
      return { type: 'invalid-remote', reason: error.message };
    }
  }

  if (
    target.startsWith('/')
    || target.startsWith('~')
    || /^[a-z]:[\\/]/i.test(target)
    || target.includes('\\')
  ) {
    return { type: 'absolute-or-platform-path' };
  }

  const hashIndex = target.indexOf('#');
  const beforeFragment = hashIndex === -1 ? target : target.slice(0, hashIndex);
  const rawFragment = hashIndex === -1 ? '' : target.slice(hashIndex + 1);
  const queryIndex = beforeFragment.indexOf('?');
  const rawPathname = queryIndex === -1 ? beforeFragment : beforeFragment.slice(0, queryIndex);
  const decodedPathname = decodeLinkComponent(rawPathname);
  const decodedFragment = decodeLinkComponent(rawFragment);
  if (decodedPathname.error || decodedFragment.error) {
    return { type: 'invalid-encoding' };
  }
  if (/[\u0000-\u001f\u007f]/.test(decodedPathname.value + decodedFragment.value)) {
    return { type: 'invalid-character' };
  }

  return {
    type: 'local',
    pathname: decodedPathname.value,
    fragment: decodedFragment.value,
  };
}

function resolveExactPath(sourceAbsolutePath, linkPathname) {
  const sourceDirectory = path.dirname(sourceAbsolutePath);
  const candidate = path.resolve(sourceDirectory, linkPathname || path.basename(sourceAbsolutePath));
  const relative = path.relative(repositoryRoot, candidate);

  if (relative === '..' || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)) {
    return { error: 'target escapes the repository' };
  }

  let current = repositoryRoot;
  const segments = relative.split(path.sep).filter(Boolean);
  for (const segment of segments) {
    if (!fs.existsSync(current) || !fs.statSync(current).isDirectory()) {
      return { error: 'target does not exist' };
    }
    const names = fs.readdirSync(current);
    if (!names.includes(segment)) {
      const caseInsensitive = names.find(
        (name) => name.toLocaleLowerCase('en-US') === segment.toLocaleLowerCase('en-US'),
      );
      if (caseInsensitive) {
        return {
          error: `target path has incorrect case; expected "${caseInsensitive}"`,
        };
      }
      return { error: 'target does not exist' };
    }
    current = path.join(current, segment);
  }

  const realRepositoryRoot = fs.realpathSync(repositoryRoot);
  const realTarget = fs.realpathSync(current);
  const realRelative = path.relative(realRepositoryRoot, realTarget);
  if (
    realRelative === '..'
    || realRelative.startsWith(`..${path.sep}`)
    || path.isAbsolute(realRelative)
  ) {
    return { error: 'target resolves outside the repository through a symbolic link' };
  }

  return { absolutePath: current, repositoryPath: normalizeRepositoryPath(current) };
}

function runSelfTests() {
  assert.equal(githubAnchor('Function / Action 系统'), 'function--action-系统');
  assert.equal(githubAnchor('Ontology Manager（运营侧）'), 'ontology-manager运营侧');
  assert.equal(githubAnchor('`tenant_id` 贯穿全链路'), 'tenant_id-贯穿全链路');

  const duplicateAnchors = collectAnchors([
    '# 标题',
    '## 标题',
    '### API / WS',
    '<a id="stable-contract"></a>',
  ]);
  assert.deepEqual(
    [...duplicateAnchors],
    ['标题', '标题-1', 'api--ws', 'stable-contract'],
  );

  const parsedMarkdown = [
    '[nested](./file_(v2).md#标题)',
    '[angle](<./file with spaces.md>)',
    '`[ignored](missing.md)`',
    '[reference][target]',
    '[target]: ./reference.md',
  ].join('\n');
  const parsed = collectLinks(markdownLines(parsedMarkdown).linkLines);
  assert.deepEqual(
    parsed.map((link) => link.target),
    ['./reference.md', './file_(v2).md#标题', './file with spaces.md'],
  );

  assert.equal(classifyLinkTarget('../../outside.md').type, 'local');
  const personalPath = ['', 'Users', 'name', 'file.md'].join('/');
  assert.equal(classifyLinkTarget(personalPath).type, 'absolute-or-platform-path');
  assert.equal(classifyLinkTarget('javascript:alert(1)').type, 'forbidden-scheme');
  assert.equal(classifyLinkTarget('https://example.com/docs').type, 'remote');
  assert.equal(classifyLinkTarget('./file.md%00').type, 'invalid-character');
  assert.equal(resolveExactPath(scriptPath, '../../README.md').repositoryPath, 'README.md');
  assert.match(resolveExactPath(scriptPath, '../../readme.md').error, /incorrect case/);
  assert.match(resolveExactPath(scriptPath, '../../../outside.md').error, /escapes/);
  process.stdout.write('Markdown checker self-tests passed.\n');
}

if (selfTest) {
  runSelfTests();
  process.exit(0);
}

const diagnostics = [];
const addDiagnostic = ({ source, line = 1, code, message, archive = false }) => {
  diagnostics.push({
    severity: archive && !strictArchive ? 'WARN' : 'ERROR',
    source,
    line,
    code,
    message,
  });
};

for (const requiredPath of requiredDocumentationFiles) {
  const absolutePath = path.join(repositoryRoot, requiredPath);
  if (!fs.existsSync(absolutePath) || !fs.statSync(absolutePath).isFile()) {
    addDiagnostic({
      source: requiredPath,
      code: 'required-file',
      message: 'required documentation index or template is missing',
      archive: isArchivePath(requiredPath),
    });
  }
}

const markdownFiles = discoverMarkdownFiles(repositoryRoot);
const documentData = new Map();
for (const absolutePath of markdownFiles) {
  const repositoryPath = normalizeRepositoryPath(absolutePath);
  const markdown = fs.readFileSync(absolutePath, 'utf8');
  const lines = markdownLines(markdown);
  documentData.set(repositoryPath, {
    absolutePath,
    repositoryPath,
    anchors: collectAnchors(lines.structuralLines),
    links: collectLinks(lines.linkLines),
  });
}

const documentationEdges = new Map();
let checkedLinkCount = 0;

for (const document of documentData.values()) {
  const archive = isArchivePath(document.repositoryPath);
  for (const link of document.links) {
    if (link.kind === 'undefined-reference') {
      addDiagnostic({
        source: document.repositoryPath,
        line: link.line,
        code: 'undefined-reference',
        message: `reference label "${link.label}" has no definition`,
        archive,
      });
      continue;
    }

    checkedLinkCount += 1;
    const classified = classifyLinkTarget(link.target);
    if (classified.type === 'remote') {
      continue;
    }
    if (classified.type === 'invalid-remote') {
      addDiagnostic({
        source: document.repositoryPath,
        line: link.line,
        code: 'remote-url',
        message: `invalid remote URL "${link.target}": ${classified.reason}`,
        archive,
      });
      continue;
    }
    if (classified.type === 'forbidden-scheme') {
      addDiagnostic({
        source: document.repositoryPath,
        line: link.line,
        code: 'forbidden-scheme',
        message: `forbidden URL scheme "${classified.protocol}:"`,
        archive,
      });
      continue;
    }
    if (classified.type === 'absolute-or-platform-path') {
      addDiagnostic({
        source: document.repositoryPath,
        line: link.line,
        code: 'absolute-path',
        message: `use a repository-relative link instead of "${link.target}"`,
        archive,
      });
      continue;
    }
    if (classified.type === 'invalid-encoding') {
      addDiagnostic({
        source: document.repositoryPath,
        line: link.line,
        code: 'url-encoding',
        message: `link contains invalid percent-encoding: "${link.target}"`,
        archive,
      });
      continue;
    }
    if (classified.type === 'invalid-character') {
      addDiagnostic({
        source: document.repositoryPath,
        line: link.line,
        code: 'url-character',
        message: `link contains a control character: "${link.target}"`,
        archive,
      });
      continue;
    }

    const resolved = resolveExactPath(document.absolutePath, classified.pathname);
    if (resolved.error) {
      addDiagnostic({
        source: document.repositoryPath,
        line: link.line,
        code: 'local-target',
        message: `${resolved.error}: "${link.target}"`,
        archive,
      });
      continue;
    }

    let anchorDocumentPath = resolved.repositoryPath;
    if (fs.statSync(resolved.absolutePath).isDirectory()) {
      const readmePath = path.join(resolved.absolutePath, 'README.md');
      if (fs.existsSync(readmePath)) {
        anchorDocumentPath = normalizeRepositoryPath(readmePath);
      }
    }

    if (
      document.repositoryPath.startsWith('docs/')
      && anchorDocumentPath.startsWith('docs/')
      && anchorDocumentPath.toLowerCase().endsWith('.md')
    ) {
      if (!documentationEdges.has(document.repositoryPath)) {
        documentationEdges.set(document.repositoryPath, new Set());
      }
      documentationEdges.get(document.repositoryPath).add(anchorDocumentPath);
    }

    if (classified.fragment === '') {
      continue;
    }
    const targetDocument = documentData.get(anchorDocumentPath);
    if (!targetDocument) {
      addDiagnostic({
        source: document.repositoryPath,
        line: link.line,
        code: 'anchor-target',
        message: `fragment points to a non-Markdown target: "${link.target}"`,
        archive,
      });
      continue;
    }
    if (!targetDocument.anchors.has(classified.fragment)) {
      addDiagnostic({
        source: document.repositoryPath,
        line: link.line,
        code: 'anchor',
        message: `anchor "#${classified.fragment}" does not exist in ${anchorDocumentPath}`,
        archive,
      });
    }
  }
}

const reachableDocumentation = new Set();
const pendingDocumentation = ['docs/README.md'];
while (pendingDocumentation.length > 0) {
  const current = pendingDocumentation.pop();
  if (reachableDocumentation.has(current)) {
    continue;
  }
  reachableDocumentation.add(current);
  for (const target of documentationEdges.get(current) ?? []) {
    if (!reachableDocumentation.has(target)) {
      pendingDocumentation.push(target);
    }
  }
}

for (const repositoryPath of documentData.keys()) {
  if (
    repositoryPath.startsWith('docs/')
    && !isArchivePath(repositoryPath)
    && !reachableDocumentation.has(repositoryPath)
  ) {
    addDiagnostic({
      source: repositoryPath,
      code: 'unreachable-doc',
      message: 'active document is not reachable from docs/README.md',
    });
  }
}

const rootIndexTargets = documentationEdges.get('docs/README.md') ?? new Set();
for (const requiredIndex of rootDocumentationIndexes) {
  if (!rootIndexTargets.has(requiredIndex)) {
    addDiagnostic({
      source: 'docs/README.md',
      code: 'missing-root-index-entry',
      message: `root documentation index must link directly to ${requiredIndex}`,
    });
  }
}

for (const rule of documentationIndexRules) {
  const prefix = `${rule.directory}/`;
  const indexTargets = documentationEdges.get(rule.index) ?? new Set();
  if (!indexTargets.has(rule.template)) {
    addDiagnostic({
      source: rule.index,
      code: 'missing-template-entry',
      message: `index must link directly to ${rule.template}`,
    });
  }
  for (const repositoryPath of documentData.keys()) {
    if (!repositoryPath.startsWith(prefix)) {
      continue;
    }
    const remainder = repositoryPath.slice(prefix.length);
    if ((!rule.recursive && remainder.includes('/')) || ['README.md', 'template.md'].includes(remainder)) {
      continue;
    }
    if (!indexTargets.has(repositoryPath)) {
      addDiagnostic({
        source: rule.index,
        code: 'missing-index-entry',
        message: `index must link directly to ${repositoryPath}`,
      });
    }
  }
}

diagnostics.sort((left, right) => (
  left.source.localeCompare(right.source)
  || left.line - right.line
  || left.code.localeCompare(right.code)
));
for (const diagnostic of diagnostics) {
  const output = `${diagnostic.severity} ${diagnostic.source}:${diagnostic.line} [${diagnostic.code}] ${diagnostic.message}\n`;
  (diagnostic.severity === 'ERROR' ? process.stderr : process.stdout).write(output);
}

const errorCount = diagnostics.filter((diagnostic) => diagnostic.severity === 'ERROR').length;
const warningCount = diagnostics.length - errorCount;
process.stdout.write(
  `Checked ${documentData.size} Markdown files and ${checkedLinkCount} links: `
  + `${errorCount} error(s), ${warningCount} warning(s).\n`,
);
process.exit(errorCount === 0 ? 0 : 1);
