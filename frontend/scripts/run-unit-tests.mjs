#!/usr/bin/env node

import { readdirSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { spawnSync } from 'node:child_process'

const testRoot = resolve('src/test/unit')

function findTests(directory) {
  return readdirSync(directory, { withFileTypes: true })
    .flatMap(entry => {
      const path = join(directory, entry.name)
      if (entry.isDirectory()) return findTests(path)
      return entry.isFile() && entry.name.endsWith('.test.ts') ? [path] : []
    })
    .sort()
}

const tests = findTests(testRoot)
if (tests.length === 0) {
  process.stderr.write('No frontend unit tests found under src/test/unit\n')
  process.exit(1)
}

const result = spawnSync(
  process.execPath,
  ['--experimental-strip-types', '--test', ...tests],
  { stdio: 'inherit' },
)

if (result.error) throw result.error
process.exit(result.status ?? 1)
