import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import {
  closeTab,
  recordVisit,
  EMPTY_NAV_TAB_STATE,
  type NavTabListState,
} from '../../../stores/tabLogic.ts'

function stateWith(tabs: Array<{ key: string; path?: string; lastUsedAt: number }>, activeKey: string | null): NavTabListState {
  return {
    tabs: tabs.map(t => ({ key: t.key, title: t.key, path: t.path ?? `/${t.key}`, lastUsedAt: t.lastUsedAt })),
    activeKey,
    owner: 'admin',
  }
}

describe('recordVisit', () => {
  it('creates a tab for a first visit and activates it', () => {
    const next = recordVisit(EMPTY_NAV_TAB_STATE, 'admin', { key: 'agent', title: '本体助手', path: '/agent' }, 1000)
    assert.deepEqual(next.tabs.map(t => t.key), ['agent'])
    assert.equal(next.activeKey, 'agent')
    assert.equal(next.owner, 'admin')
    assert.equal(next.tabs[0].lastUsedAt, 1000)
  })

  it('appends tabs in open order', () => {
    let state = recordVisit(EMPTY_NAV_TAB_STATE, 'admin', { key: 'agent', title: '本体助手', path: '/agent' }, 1000)
    state = recordVisit(state, 'admin', { key: 'ontologies', title: '本体管理', path: '/ontologies' }, 2000)
    assert.deepEqual(state.tabs.map(t => t.key), ['agent', 'ontologies'])
    assert.equal(state.activeKey, 'ontologies')
  })

  it('reuses the same tab for in-domain navigation, updating title/path/lastUsedAt', () => {
    let state = recordVisit(EMPTY_NAV_TAB_STATE, 'admin', { key: 'agent', title: '本体助手', path: '/agent' }, 1000)
    state = recordVisit(state, 'admin', { key: 'ontologies', title: '本体管理', path: '/ontologies' }, 2000)
    state = recordVisit(state, 'admin', { key: 'ontologies', title: '本体管理 · 详情', path: '/ontologies/123' }, 3000)
    assert.equal(state.tabs.length, 2)
    assert.deepEqual(state.tabs.map(t => t.key), ['agent', 'ontologies'])
    const tab = state.tabs[1]
    assert.equal(tab.title, '本体管理 · 详情')
    assert.equal(tab.path, '/ontologies/123')
    assert.equal(tab.lastUsedAt, 3000)
    assert.equal(state.activeKey, 'ontologies')
  })

  it('resets the tab list when the signed-in user changes', () => {
    const before = recordVisit(EMPTY_NAV_TAB_STATE, 'admin', { key: 'agent', title: '本体助手', path: '/agent' }, 1000)
    const next = recordVisit(before, 'viewer', { key: 'overview', title: '平台概览', path: '/overview' }, 2000)
    assert.deepEqual(next.tabs.map(t => t.key), ['overview'])
    assert.equal(next.activeKey, 'overview')
    assert.equal(next.owner, 'viewer')
  })
})

describe('closeTab', () => {
  it('removes a non-active tab without touching the active one', () => {
    const state = stateWith([
      { key: 'agent', lastUsedAt: 1000 },
      { key: 'ontologies', lastUsedAt: 2000 },
    ], 'ontologies')
    const result = closeTab(state, 'agent')
    assert.equal(result.closedActive, false)
    assert.equal(result.nextPath, null)
    assert.deepEqual(result.state.tabs.map(t => t.key), ['ontologies'])
    assert.equal(result.state.activeKey, 'ontologies')
  })

  it('falls back to the most recently used tab when closing the active one', () => {
    const state = stateWith([
      { key: 'agent', lastUsedAt: 3000 },
      { key: 'ontologies', lastUsedAt: 2000 },
      { key: 'models', lastUsedAt: 1000 },
    ], 'ontologies')
    const result = closeTab(state, 'ontologies')
    assert.equal(result.closedActive, true)
    assert.equal(result.state.activeKey, 'agent')
    assert.equal(result.nextPath, '/agent')
    assert.deepEqual(result.state.tabs.map(t => t.key), ['agent', 'models'])
  })

  it('returns null nextPath when the last tab is closed', () => {
    const state = stateWith([{ key: 'agent', lastUsedAt: 1000 }], 'agent')
    const result = closeTab(state, 'agent')
    assert.equal(result.closedActive, true)
    assert.equal(result.nextPath, null)
    assert.equal(result.state.activeKey, null)
    assert.equal(result.state.tabs.length, 0)
  })

  it('is a no-op for an unknown key', () => {
    const state = stateWith([{ key: 'agent', lastUsedAt: 1000 }], 'agent')
    const result = closeTab(state, 'missing')
    assert.equal(result.closedActive, false)
    assert.equal(result.state, state)
  })
})
