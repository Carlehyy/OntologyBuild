import assert from 'node:assert/strict'
import { describe, it } from 'node:test'
import resolveConfig from 'tailwindcss/resolveConfig.js'

import config from '../../../tailwind.config.ts'


describe('Tailwind compatibility configuration', () => {
  it('preserves the default typography and radius scale used before config consolidation', () => {
    const resolved = resolveConfig(config)

    assert.deepEqual(config.content, [
      './index.html',
      './src/**/*.{ts,tsx,js,jsx}',
    ])
    assert.deepEqual(resolved.theme.fontSize.sm, ['0.875rem', { lineHeight: '1.25rem' }])
    assert.deepEqual(resolved.theme.fontSize.base, ['1rem', { lineHeight: '1.5rem' }])
    assert.equal(resolved.theme.borderRadius.md, '0.375rem')
    assert.equal(resolved.theme.borderRadius.lg, '0.5rem')
  })
})
