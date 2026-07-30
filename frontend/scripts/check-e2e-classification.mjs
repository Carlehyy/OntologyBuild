import { readdirSync, readFileSync } from 'node:fs'
import { basename, dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const suiteConfigs = [
  'playwright.mocked.config.ts',
  'playwright.stack.config.ts',
  'playwright.external.config.ts',
]
const frontendRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const testDir = join(frontendRoot, 'src', 'test', 'e2e')
const specs = readdirSync(testDir)
  .filter((name) => name.endsWith('.spec.ts'))
  .sort()
const assignments = new Map()

for (const configPath of suiteConfigs) {
  const config = readFileSync(join(frontendRoot, configPath), 'utf8')
  const matches = config.matchAll(/\*\*\/([A-Za-z0-9_-]+\.spec\.ts)/g)
  for (const match of matches) {
    const spec = basename(match[1])
    const owners = assignments.get(spec) ?? []
    owners.push(configPath)
    assignments.set(spec, owners)
  }
}

const missing = specs.filter((spec) => !assignments.has(spec))
const duplicates = [...assignments]
  .filter(([, owners]) => owners.length !== 1)
  .map(([spec, owners]) => `${spec}: ${owners.join(', ')}`)
const unknown = [...assignments.keys()].filter((spec) => !specs.includes(spec))
const realSuiteConfigs = new Set([
  'playwright.stack.config.ts',
  'playwright.external.config.ts',
])
const credentialViolations = []

for (const [spec, owners] of assignments) {
  if (owners.length !== 1 || !realSuiteConfigs.has(owners[0])) continue
  const source = readFileSync(join(testDir, spec), 'utf8')
  if (!source.includes("from './support/stack-credentials'")) {
    credentialViolations.push(`${spec}: missing shared stack credential import`)
  }
  if (
    /PLAYWRIGHT_ADMIN_(?:USER|PASSWORD)/.test(source)
    || /\badmin123\b/.test(source)
  ) {
    credentialViolations.push(`${spec}: defines or embeds stack credentials locally`)
  }
}

if (
  missing.length
  || duplicates.length
  || unknown.length
  || credentialViolations.length
) {
  if (missing.length) console.error(`Unclassified E2E specs: ${missing.join(', ')}`)
  if (duplicates.length) console.error(`Multiply classified E2E specs: ${duplicates.join('; ')}`)
  if (unknown.length) console.error(`Unknown E2E specs in configs: ${unknown.join(', ')}`)
  if (credentialViolations.length) {
    console.error(`Invalid real-suite credentials: ${credentialViolations.join('; ')}`)
  }
  process.exit(1)
}

const realSpecCount = [...assignments.values()]
  .filter((owners) => owners.length === 1 && realSuiteConfigs.has(owners[0]))
  .length
console.log(
  `Classified ${specs.length} E2E specs exactly once across `
  + `${suiteConfigs.length} suites; ${realSpecCount} real-suite specs use shared credentials.`,
)
