import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import { buildTabTitles, menuKeyForPath, tabSuffixForPath } from '../../../stores/tabTitle.ts'

describe('tabSuffixForPath', () => {
  it('本体列表与新建页没有页面级后缀', () => {
    assert.equal(tabSuffixForPath('/ontologies'), null)
    assert.equal(tabSuffixForPath('/ontologies/new'), null)
  })

  it('本体详情域内各页面返回自身描述', () => {
    assert.equal(tabSuffixForPath('/ontologies/123'), '详情')
    assert.equal(tabSuffixForPath('/ontologies/123/mapping-config'), '映射配置')
    assert.equal(tabSuffixForPath('/ontologies/123/graph'), '图谱')
    assert.equal(tabSuffixForPath('/ontologies/123/entities/e1'), '详情')
    assert.equal(tabSuffixForPath('/ontologies/123/logic/l1'), '详情')
    assert.equal(tabSuffixForPath('/ontologies/123/actions/a1'), '详情')
  })

  it('世界模型开发页按实际路由返回开发后缀（历史正则与路由不匹配）', () => {
    assert.equal(tabSuffixForPath('/world-model/models/m1/develop'), '开发')
    assert.equal(tabSuffixForPath('/world-model/models/m1/develop/debug'), '开发')
    assert.equal(tabSuffixForPath('/world-model/models'), null)
    assert.equal(tabSuffixForPath('/world-model/calls'), null)
  })

  it('报告/数据管家/脚本页面后缀', () => {
    assert.equal(tabSuffixForPath('/agent/reports'), '报告')
    assert.equal(tabSuffixForPath('/agent/reports/t1'), '报告')
    assert.equal(tabSuffixForPath('/agent'), null)
    assert.equal(tabSuffixForPath('/data/pipelines/steward'), '数据管家')
    assert.equal(tabSuffixForPath('/data/pipelines/script/p1'), '脚本')
    assert.equal(tabSuffixForPath('/data/pipelines'), null)
  })

  it('系统设置各子页按页面名返回', () => {
    assert.equal(tabSuffixForPath('/settings/users'), '用户管理')
    assert.equal(tabSuffixForPath('/settings/agents'), '智能体配置')
    assert.equal(tabSuffixForPath('/settings/domains'), '领域设置')
    assert.equal(tabSuffixForPath('/settings/monitoring'), '运行监控')
    assert.equal(tabSuffixForPath('/settings/unknown'), null)
  })
})

describe('buildTabTitles', () => {
  it('有后缀时：可见标题只留页面一层，完整标题保留两级', () => {
    assert.deepEqual(buildTabTitles('本体管理', '详情'), {
      title: '详情',
      fullTitle: '本体管理 · 详情',
    })
  })

  it('无后缀时：两者都是菜单名', () => {
    assert.deepEqual(buildTabTitles('本体管理', null), {
      title: '本体管理',
      fullTitle: '本体管理',
    })
  })

  it('label 为 null 时两者均为 null', () => {
    assert.deepEqual(buildTabTitles(null, '详情'), { title: null, fullTitle: null })
    assert.deepEqual(buildTabTitles(null, null), { title: null, fullTitle: null })
  })
})

describe('menuKeyForPath', () => {
  it('分辨各菜单域与未知路径', () => {
    assert.equal(menuKeyForPath('/ontologies/123'), 'ontologies')
    assert.equal(menuKeyForPath('/settings/users'), 'system_settings')
    assert.equal(menuKeyForPath('/world-model/models/m1/develop'), 'world_model.models')
    assert.equal(menuKeyForPath('/world-model/calls'), 'world_model.calls')
    assert.equal(menuKeyForPath('/data/pipelines/sync-tasks'), 'data.sync_tasks')
    assert.equal(menuKeyForPath('/data/structured'), 'data.structured')
    assert.equal(menuKeyForPath('/data/pipelines/steward'), 'data.pipelines')
    assert.equal(menuKeyForPath('/agent/reports'), 'agent')
    assert.equal(menuKeyForPath('/api-hub/interfaces'), 'api_hub.interfaces')
    assert.equal(menuKeyForPath('/no-such-page'), null)
  })
})

