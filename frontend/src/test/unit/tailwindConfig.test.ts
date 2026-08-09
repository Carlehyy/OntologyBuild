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

  it('enables class-based dark mode and maps shadcn semantic colors to CSS variables', () => {
    const resolved = resolveConfig(config)
    const colors = resolved.theme.colors as unknown as Record<string, string | Record<string, string>>

    assert.deepEqual(config.darkMode, ['class'])
    assert.equal(colors.background, 'var(--background)')
    assert.equal(colors.foreground, 'var(--foreground)')
    assert.equal((colors.primary as Record<string, string>).DEFAULT, 'var(--primary)')
    assert.equal((colors.primary as Record<string, string>).foreground, 'var(--primary-foreground)')
    assert.equal((colors.muted as Record<string, string>).DEFAULT, 'var(--muted)')
    assert.equal((colors.muted as Record<string, string>).foreground, 'var(--muted-foreground)')
    assert.equal((colors.destructive as Record<string, string>).DEFAULT, 'var(--destructive)')
    assert.equal(colors.border, 'var(--border)')
    assert.equal(colors.input, 'var(--input)')
    assert.equal(colors.ring, 'var(--ring)')
  })
})
