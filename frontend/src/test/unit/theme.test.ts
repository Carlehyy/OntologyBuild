import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import {
  applyThemeClass,
  DEFAULT_THEME,
  normalizeTheme,
  parseStoredTheme,
} from '../../lib/theme.ts'


describe('theme preference', () => {
  it('defaults to light so the platform appearance stays unchanged for existing users', () => {
    assert.equal(DEFAULT_THEME, 'light')
    assert.equal(normalizeTheme(undefined), 'light')
    assert.equal(normalizeTheme(null), 'light')
    assert.equal(normalizeTheme(''), 'light')
    assert.equal(normalizeTheme('system'), 'light')
    assert.equal(normalizeTheme(0), 'light')
    assert.equal(normalizeTheme({ theme: 'dark' }), 'light')
  })

  it('normalizes only the two supported themes', () => {
    assert.equal(normalizeTheme('light'), 'light')
    assert.equal(normalizeTheme('dark'), 'dark')
    assert.equal(normalizeTheme('Dark'), 'light')
  })

  it('parses the zustand persist envelope written by the theme store', () => {
    assert.equal(parseStoredTheme('{"state":{"theme":"dark"},"version":0}'), 'dark')
    assert.equal(parseStoredTheme('{"state":{"theme":"light"},"version":0}'), 'light')
  })

  it('tolerates raw string values and falls back on malformed payloads', () => {
    assert.equal(parseStoredTheme('dark'), 'dark')
    assert.equal(parseStoredTheme('light'), 'light')
    assert.equal(parseStoredTheme(null), 'light')
    assert.equal(parseStoredTheme(undefined), 'light')
    assert.equal(parseStoredTheme(''), 'light')
    assert.equal(parseStoredTheme('not-json'), 'light')
    assert.equal(parseStoredTheme('{"state":{}}'), 'light')
    assert.equal(parseStoredTheme('{"state":{"theme":"blue"}}'), 'light')
    assert.equal(parseStoredTheme('[1,2,3]'), 'light')
    assert.equal(parseStoredTheme('null'), 'light')
  })

  it('applies the dark class only for the dark theme', () => {
    const toggles: Array<[string, boolean | undefined]> = []
    const root = {
      classList: {
        toggle: (name: string, force?: boolean) => {
          toggles.push([name, force])
        },
      },
    }

    applyThemeClass('dark', root)
    applyThemeClass('light', root)

    assert.deepEqual(toggles, [
      ['dark', true],
      ['dark', false],
    ])
  })
})
