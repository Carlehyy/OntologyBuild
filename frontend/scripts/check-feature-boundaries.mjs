import { readdirSync, readFileSync } from 'node:fs'
import { dirname, extname, join, relative, resolve, sep } from 'node:path'
import { fileURLToPath } from 'node:url'

const frontendRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const srcRoot = join(frontendRoot, 'src')
const overviewRoot = join(srcRoot, 'features', 'overview')
const pagesRoot = join(srcRoot, 'pages')
const legacyOverviewRoot = join(srcRoot, 'pages', 'overview')
const stewardRoot = join(srcRoot, 'pages', 'pipelines', 'steward')
const stewardPage = join(stewardRoot, 'DataStewardPage.tsx')
const stewardTimeline = join(
  stewardRoot,
  'components',
  'ConversationTimeline.tsx',
)
const stewardComposer = join(
  stewardRoot,
  'components',
  'StewardComposer.tsx',
)
const stewardModel = join(stewardRoot, 'stewardModel.ts')
const errors = []

function sourceFiles(root) {
  return readdirSync(root, { withFileTypes: true }).flatMap(entry => {
    const path = join(root, entry.name)
    if (entry.isDirectory()) return sourceFiles(path)
    return /\.(?:[cm]?[jt]sx?)$/.test(entry.name) ? [path] : []
  })
}

function importSpecifiers(source) {
  const patterns = [
    /\bfrom\s*['"]([^'"]+)['"]/g,
    /\bimport\s*['"]([^'"]+)['"]/g,
    /\bimport\s*\(\s*['"]([^'"]+)['"]\s*\)/g,
    /\brequire\s*\(\s*['"]([^'"]+)['"]\s*\)/g,
  ]
  return [...new Set(patterns.flatMap(pattern =>
    [...source.matchAll(pattern)].map(match => match[1])
  ))]
}

function sourceRelativeTarget(sourceFile, specifier) {
  if (specifier.startsWith('@/')) return specifier.slice(2)
  if (!specifier.startsWith('.')) return null
  return relative(srcRoot, resolve(dirname(sourceFile), specifier)).split(sep).join('/')
}

function resolveSourceTarget(sourceFile, specifier, sourceSet) {
  let base
  if (specifier.startsWith('@/')) {
    base = join(srcRoot, specifier.slice(2))
  } else if (specifier.startsWith('.')) {
    base = resolve(dirname(sourceFile), specifier)
  } else {
    return null
  }

  const candidates = extname(base)
    ? [base]
    : [
        `${base}.ts`,
        `${base}.tsx`,
        `${base}.js`,
        `${base}.jsx`,
        join(base, 'index.ts'),
        join(base, 'index.tsx'),
        join(base, 'index.js'),
        join(base, 'index.jsx'),
      ]
  return candidates.find(candidate => sourceSet.has(candidate)) ?? null
}

for (const sourceFile of sourceFiles(overviewRoot)) {
  const source = readFileSync(sourceFile, 'utf8')
  for (const specifier of importSpecifiers(source)) {
    const target = sourceRelativeTarget(sourceFile, specifier)
    if (target === null) continue
    const dependsOnPages = target === 'pages' || target.startsWith('pages/')
    const dependsOnAnotherFeature = (
      (target === 'features' || target.startsWith('features/'))
      && target !== 'features/overview'
      && !target.startsWith('features/overview/')
    )
    if (dependsOnPages || dependsOnAnotherFeature) {
      errors.push(
        `${relative(frontendRoot, sourceFile)} imports forbidden dependency ${specifier}`,
      )
    }
  }
}

const pageSourceFiles = sourceFiles(pagesRoot)
const pageDomains = new Set()
for (const sourceFile of pageSourceFiles) {
  const sourceParts = relative(srcRoot, sourceFile).split(sep)
  const sourceDomain = sourceParts[1]
  if (!sourceDomain) continue
  pageDomains.add(sourceDomain)

  const source = readFileSync(sourceFile, 'utf8')
  for (const specifier of importSpecifiers(source)) {
    const target = sourceRelativeTarget(sourceFile, specifier)
    if (target === null) continue
    const targetParts = target.split('/')
    const targetDomain = targetParts[0] === 'pages' ? targetParts[1] : null
    if (targetDomain && targetDomain !== sourceDomain) {
      errors.push(
        `${relative(frontendRoot, sourceFile)} imports sibling page domain ${specifier}`,
      )
    }
  }
}

const legacyFiles = readdirSync(legacyOverviewRoot)
  .filter(name => !name.startsWith('.'))
  .sort()
if (legacyFiles.join(',') !== 'OverviewPage.tsx') {
  errors.push(
    `src/pages/overview must contain only OverviewPage.tsx; found: ${legacyFiles.join(', ') || '(empty)'}`,
  )
}

const legacyFacade = readFileSync(join(legacyOverviewRoot, 'OverviewPage.tsx'), 'utf8')
if (!legacyFacade.includes(
  "export { default } from '@/features/overview/OverviewPage'",
)) {
  errors.push('src/pages/overview/OverviewPage.tsx must remain a compatibility re-export')
}
if (/\b(?:function|class|interface|type|const|let|var)\b/.test(
  legacyFacade.replace(/\/\/.*$/gm, ''),
)) {
  errors.push('src/pages/overview/OverviewPage.tsx must not contain implementation code')
}

const appSource = readFileSync(join(srcRoot, 'App.tsx'), 'utf8')
if (!appSource.includes("from '@/features/overview'")) {
  errors.push('src/App.tsx must import overview through @/features/overview')
}
if (appSource.includes('@/pages/overview')) {
  errors.push('src/App.tsx must not import the overview compatibility facade')
}

const stewardSources = new Map([
  [stewardPage, readFileSync(stewardPage, 'utf8')],
  [stewardTimeline, readFileSync(stewardTimeline, 'utf8')],
  [stewardComposer, readFileSync(stewardComposer, 'utf8')],
  [stewardModel, readFileSync(stewardModel, 'utf8')],
])
const stewardLineLimits = new Map([
  [stewardPage, 650],
  [stewardTimeline, 450],
  [stewardComposer, 420],
  [stewardModel, 130],
])
for (const [sourceFile, maximum] of stewardLineLimits) {
  const lineCount = stewardSources.get(sourceFile).split(/\r?\n/).length
  if (lineCount > maximum) {
    errors.push(
      `${relative(frontendRoot, sourceFile)} has ${lineCount} lines; `
      + `the Steward responsibility limit is ${maximum}`,
    )
  }
}

const stewardPageSource = stewardSources.get(stewardPage)
for (const requiredImport of [
  "from './components/ConversationTimeline'",
  "from './components/StewardComposer'",
  "from './stewardModel'",
]) {
  if (!stewardPageSource.includes(requiredImport)) {
    errors.push(`DataStewardPage must use its direct boundary ${requiredImport}`)
  }
}
for (const presentationToken of [
  'ReactMarkdown',
  'TOOL_META',
  'data-testid="steward-composer-shell"',
]) {
  if (stewardPageSource.includes(presentationToken)) {
    errors.push(
      `DataStewardPage contains presentation responsibility ${presentationToken}`,
    )
  }
}
for (const orchestrationToken of [
  'stewardApi',
  'streamStewardChat',
  'AbortController',
  'loadRecords',
]) {
  if (!stewardPageSource.includes(orchestrationToken)) {
    errors.push(
      `DataStewardPage lost orchestration responsibility ${orchestrationToken}`,
    )
  }
}

for (const sourceFile of [stewardTimeline, stewardComposer]) {
  const source = stewardSources.get(sourceFile)
  for (const forbiddenToken of [
    'stewardApi',
    'streamStewardChat',
    'downloadStewardConversation',
    'downloadStewardFile',
    'DataStewardPage',
  ]) {
    if (source.includes(forbiddenToken)) {
      errors.push(
        `${relative(frontendRoot, sourceFile)} depends on forbidden `
        + `Steward orchestration ${forbiddenToken}`,
      )
    }
  }
}
const stewardTimelineSource = stewardSources.get(stewardTimeline)
for (const statefulToken of ['useState', 'useEffect', 'useLayoutEffect']) {
  if (stewardTimelineSource.includes(statefulToken)) {
    errors.push(
      `ConversationTimeline must remain presentational; found ${statefulToken}`,
    )
  }
}
const stewardModelSource = stewardSources.get(stewardModel)
for (const forbiddenToken of [
  "from 'react'",
  'DataStewardPage',
  '/components/',
  'stewardApi',
  'streamStewardChat',
]) {
  if (stewardModelSource.includes(forbiddenToken)) {
    errors.push(
      `stewardModel must remain a pure lower-level dependency; found `
      + forbiddenToken,
    )
  }
}

const productionFiles = sourceFiles(srcRoot).filter(sourceFile => {
  const path = relative(srcRoot, sourceFile).split(sep).join('/')
  return path !== 'test' && !path.startsWith('test/')
})
const productionSet = new Set(productionFiles)
const dependencyGraph = new Map(productionFiles.map(sourceFile => {
  const dependencies = importSpecifiers(readFileSync(sourceFile, 'utf8'))
    .map(specifier => resolveSourceTarget(sourceFile, specifier, productionSet))
    .filter(Boolean)
  return [sourceFile, dependencies]
}))

const reachable = new Set()
const pending = [join(srcRoot, 'main.tsx')]
while (pending.length > 0) {
  const sourceFile = pending.pop()
  if (!sourceFile || reachable.has(sourceFile)) continue
  reachable.add(sourceFile)
  pending.push(...(dependencyGraph.get(sourceFile) ?? []))
}

const allowedUnreachable = new Set([join(legacyOverviewRoot, 'OverviewPage.tsx')])
for (const sourceFile of productionFiles) {
  if (!reachable.has(sourceFile) && !allowedUnreachable.has(sourceFile)) {
    errors.push(
      `production source is unreachable from src/main.tsx: ${relative(frontendRoot, sourceFile)}`,
    )
  }
}
for (const sourceFile of allowedUnreachable) {
  if (!productionSet.has(sourceFile)) {
    errors.push(
      `documented compatibility source is missing: ${relative(frontendRoot, sourceFile)}`,
    )
  }
}

const visitState = new Map()
const visitPath = []
function visitDependency(sourceFile) {
  visitState.set(sourceFile, 'visiting')
  visitPath.push(sourceFile)
  for (const dependency of dependencyGraph.get(sourceFile) ?? []) {
    if (visitState.get(dependency) === 'visiting') {
      const cycleStart = visitPath.indexOf(dependency)
      const cycle = [...visitPath.slice(cycleStart), dependency]
        .map(path => relative(frontendRoot, path))
        .join(' -> ')
      errors.push(`production dependency cycle: ${cycle}`)
    } else if (!visitState.has(dependency)) {
      visitDependency(dependency)
    }
  }
  visitPath.pop()
  visitState.set(sourceFile, 'visited')
}
for (const sourceFile of productionFiles) {
  if (!visitState.has(sourceFile)) visitDependency(sourceFile)
}

if (errors.length) {
  for (const error of errors) console.error(`ERROR [feature-boundaries] ${error}`)
  process.exit(1)
}

console.log(
  `Feature boundaries passed: checked ${sourceFiles(overviewRoot).length} overview source files, ${pageSourceFiles.length} page files across ${pageDomains.size} page domains, and ${productionFiles.length} production modules with no undocumented orphan or dependency cycle.`,
)
