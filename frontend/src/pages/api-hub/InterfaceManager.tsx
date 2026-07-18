import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Braces, Check, ChevronRight, CirclePlus, Copy, FileCode2, Folder, Play,
  Plus, Search, Send, Trash2, X, Database, Globe2, GripVertical, KeyRound, Share2,
  LoaderCircle,
} from 'lucide-react'
import { apiError, apiHub, emptyHubInterface, type HubInterface, type KV, type RunResult } from '@/api/apiHub'
import { Button } from '@/components/ui/Button'
import { ConfirmModal, Modal } from '@/components/ui/Modal'
import {
  OpenInterfacesModal, ProxyKeysModal, SystemDataModal,
} from './InterfaceDataModals'
import { HttpPublicationModal } from './HttpPublicationModal'
import { buildProxyCallExample } from './proxyCallExample'

interface Props {
  interfaces: HubInterface[]
  reload: () => Promise<HubInterface[]>
  onError: (message: string) => void
}

const methods = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS']
const methodTone: Record<string, string> = {
  GET: 'text-blue-600 bg-blue-50', POST: 'text-emerald-700 bg-emerald-50',
  PUT: 'text-amber-700 bg-amber-50', PATCH: 'text-violet-700 bg-violet-50',
  DELETE: 'text-red-600 bg-red-50', HEAD: 'text-slate-600 bg-slate-100',
  OPTIONS: 'text-cyan-700 bg-cyan-50',
}

type PendingNavigation =
  | { type: 'select'; item: HubInterface }
  | { type: 'create' }

type DropTarget = { group: string; index: number }

export default function InterfaceManager({ interfaces, reload, onError }: Props) {
  const [selectedId, setSelectedId] = useState<number | null>(interfaces[0]?.id ?? null)
  const [draft, setDraft] = useState<HubInterface>(() => interfaces[0] ? structuredClone(interfaces[0]) : emptyHubInterface())
  const [baseline, setBaseline] = useState<HubInterface>(() => interfaces[0] ? structuredClone(interfaces[0]) : emptyHubInterface())
  const [search, setSearch] = useState('')
  const [editorTab, setEditorTab] = useState<'params' | 'headers' | 'body' | 'description'>('params')
  const [saving, setSaving] = useState(false)
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState<RunResult | null>(null)
  const [resultFingerprint, setResultFingerprint] = useState('')
  const [pendingNavigation, setPendingNavigation] = useState<PendingNavigation | null>(null)
  const [deleteOpen, setDeleteOpen] = useState(false)
  const [callExampleDraft, setCallExampleDraft] = useState<HubInterface | null>(null)
  const [callExampleCookie, setCallExampleCookie] = useState('')
  const [includeLogin, setIncludeLogin] = useState(false)
  const [cookieLoading, setCookieLoading] = useState(false)
  const [cookieMessage, setCookieMessage] = useState('')
  const [callExampleCopied, setCallExampleCopied] = useState(false)
  const [draggingId, setDraggingId] = useState<number | null>(null)
  const [dropTarget, setDropTarget] = useState<DropTarget | null>(null)
  const [openInterfaces, setOpenInterfaces] = useState(false)
  const [publicationTarget, setPublicationTarget] = useState<HubInterface | null>(null)
  const [publicationCopying, setPublicationCopying] = useState(false)
  const [publicationCopied, setPublicationCopied] = useState(false)
  const [proxyKeys, setProxyKeys] = useState(false)
  const [systemData, setSystemData] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)
  const [sizes, setSizes] = useState<[number, number]>([28, 72])

  const startResize = useCallback((event: React.PointerEvent<HTMLDivElement>) => {
    event.preventDefault()
    const rect = containerRef.current?.getBoundingClientRect()
    if (!rect) return
    const startX = event.clientX
    const start = sizes
    const previousCursor = document.body.style.cursor
    const previousSelect = document.body.style.userSelect
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
    const onMove = (moveEvent: PointerEvent) => {
      const delta = ((moveEvent.clientX - startX) / rect.width) * 100
      const left = Math.min(42, Math.max(20, start[0] + delta))
      setSizes([left, 100 - left])
    }
    const onUp = () => {
      document.body.style.cursor = previousCursor
      document.body.style.userSelect = previousSelect
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
  }, [sizes])

  const filtered = useMemo(() => {
    const query = search.trim().toLowerCase()
    return query ? interfaces.filter(item => `${item.name} ${item.url} ${item.method} ${item.group_name}`.toLowerCase().includes(query)) : interfaces
  }, [interfaces, search])
  const grouped = useMemo(() => {
    const groups = new Map<string, HubInterface[]>()
    filtered.forEach(item => {
      const key = item.group_name || ''
      groups.set(key, [...(groups.get(key) || []), item])
    })
    return [...groups.entries()].sort(([a], [b]) => a === '' ? 1 : b === '' ? -1 : a.localeCompare(b, 'zh-CN'))
  }, [filtered])
  const groupNames = [...new Set(interfaces.map(item => item.group_name).filter(Boolean))].sort()
  const isDirty = draftFingerprint(draft) !== draftFingerprint(baseline)
  const resultStale = Boolean(
    result && resultFingerprint !== requestFingerprint(draft)
  )
  const callExample = useMemo(
    () => callExampleDraft ? buildCallExample(callExampleDraft, includeLogin ? callExampleCookie : '') : '',
    [callExampleCookie, callExampleDraft, includeLogin],
  )

  const selectNow = (item: HubInterface) => {
    setSelectedId(item.id)
    setDraft(structuredClone(item))
    setBaseline(structuredClone(item))
    setResult(null)
    setResultFingerprint('')
  }
  const createNow = () => {
    setSelectedId(null)
    setDraft(emptyHubInterface())
    setBaseline(emptyHubInterface())
    setResult(null)
    setResultFingerprint('')
  }
  const select = (item: HubInterface) => {
    if (item.id === selectedId) return
    if (isDirty) { setPendingNavigation({ type: 'select', item }); return }
    selectNow(item)
  }
  const create = () => {
    if (isDirty) { setPendingNavigation({ type: 'create' }); return }
    createNow()
  }
  const discardAndNavigate = () => {
    if (!pendingNavigation) return
    if (pendingNavigation.type === 'select') selectNow(pendingNavigation.item)
    else createNow()
    setPendingNavigation(null)
  }
  const patchDraft = <K extends keyof HubInterface>(key: K, value: HubInterface[K]) => setDraft(current => ({ ...current, [key]: value }))

  useEffect(() => {
    if (!isDirty) return undefined
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault()
      event.returnValue = ''
    }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [isDirty])

  useEffect(() => setPublicationCopied(false), [selectedId])

  const save = async (): Promise<HubInterface | null> => {
    if (!draft.name.trim()) { onError('请填写接口名称'); return null }
    if (!draft.url.trim()) { onError('请填写请求 URL'); return null }
    setSaving(true)
    try {
      const payload = { ...draft, method: draft.method.toUpperCase() }
      const saved = draft.id
        ? await apiHub.updateInterface(draft.id, payload)
        : await apiHub.createInterface(payload)
      setSelectedId(saved.id)
      setDraft(structuredClone(saved))
      setBaseline(structuredClone(saved))
      await reload()
      return saved
    } catch (error) {
      onError(apiError(error)); return null
    } finally { setSaving(false) }
  }

  const reloadOpenState = async () => {
    const items = await reload()
    const refreshed = items.find(item => item.id === selectedId)
    if (refreshed) {
      setDraft(current => ({ ...current, open_enabled: refreshed.open_enabled }))
      setBaseline(current => ({ ...current, open_enabled: refreshed.open_enabled }))
    }
    return items
  }

  const reloadPublication = async () => {
    const items = await reload()
    const refreshed = items.find(item => item.id === selectedId)
    if (refreshed) {
      const publication = {
        http_enabled: refreshed.http_enabled,
        proxy_slug: refreshed.proxy_slug,
        proxy_query_keys: refreshed.proxy_query_keys,
        proxy_header_keys: refreshed.proxy_header_keys,
        proxy_body_enabled: refreshed.proxy_body_enabled,
        proxy_body_keys: refreshed.proxy_body_keys,
      }
      setDraft(current => ({ ...current, ...publication }))
      setBaseline(current => ({ ...current, ...publication }))
    }
    return items
  }

  const run = async () => {
    if (!draft.name.trim()) { onError('请填写接口名称'); return }
    if (!draft.url.trim()) { onError('请填写请求 URL'); return }
    setRunning(true)
    try {
      const payload = { ...draft, method: draft.method.toUpperCase() }
      const fingerprint = requestFingerprint(payload)
      setResult(await apiHub.runDraft(payload))
      setResultFingerprint(fingerprint)
    } catch (error) { onError(apiError(error)) }
    finally { setRunning(false) }
  }

  const remove = async () => {
    if (!draft.id) return
    setSaving(true)
    try {
      await apiHub.deleteInterface(draft.id)
      const items = await reload()
      const next = items[0]
      setSelectedId(next?.id ?? null)
      setDraft(next ? structuredClone(next) : emptyHubInterface())
      setBaseline(next ? structuredClone(next) : emptyHubInterface())
      setResult(null)
      setDeleteOpen(false)
    } catch (error) { onError(apiError(error)) }
    finally { setSaving(false) }
  }

  const showCallExample = () => {
    try {
      buildCallExample(draft, '')
      setCallExampleDraft(structuredClone(draft))
      setCallExampleCookie('')
      setIncludeLogin(false)
      setCookieMessage('')
      setCallExampleCopied(false)
    } catch {
      onError('请先填写有效的绝对 URL，再查看调用示例')
    }
  }

  const toggleExampleLogin = async (checked: boolean) => {
    setCallExampleCopied(false)
    if (!checked) { setIncludeLogin(false); return }
    if (callExampleCookie) { setIncludeLogin(true); return }
    setCookieLoading(true)
    setCookieMessage('')
    try {
      const value = await apiHub.credentialCookieHeader()
      if (!value.cookie) {
        setIncludeLogin(false)
        setCookieMessage('当前没有可用的 W3 登录态，请先在“授权配置”中刷新登录。')
        return
      }
      setCallExampleCookie(value.cookie)
      setIncludeLogin(true)
    } catch (error) {
      setIncludeLogin(false)
      setCookieMessage(apiError(error))
    } finally { setCookieLoading(false) }
  }

  const moveInterface = async (targetGroup: string, rawTargetIndex: number) => {
    if (!draggingId) return
    const moving = interfaces.find(item => item.id === draggingId)
    if (!moving) return
    const targetItems = interfaces.filter(item => (item.group_name || '') === targetGroup)
    let targetIndex = rawTargetIndex
    if ((moving.group_name || '') === targetGroup) {
      const sourceIndex = targetItems.findIndex(item => item.id === draggingId)
      if (sourceIndex >= 0 && sourceIndex < targetIndex) targetIndex -= 1
    }
    setDropTarget(null)
    try {
      await apiHub.moveInterface(draggingId, { group_name: targetGroup, target_index: targetIndex })
      const items = await reload()
      if (selectedId === draggingId) {
        const moved = items.find(item => item.id === draggingId)
        if (moved) {
          setDraft(structuredClone(moved))
          setBaseline(structuredClone(moved))
        }
      }
    } catch (error) { onError(apiError(error)) }
    finally { setDraggingId(null) }
  }

  const startInterfaceDrag = (event: React.DragEvent<HTMLDivElement>, item: HubInterface) => {
    if (isDirty) {
      event.preventDefault()
      onError('请先保存当前接口的修改，再调整接口顺序')
      return
    }
    if (!item.id || search.trim()) { event.preventDefault(); return }
    setDraggingId(item.id)
    event.dataTransfer.effectAllowed = 'move'
    event.dataTransfer.setData('text/plain', String(item.id))
  }

  const copyPublishedExample = async () => {
    if (!draft.id) return
    setPublicationCopying(true)
    try {
      const [saved, info] = await Promise.all([
        apiHub.getInterface(draft.id),
        apiHub.proxyInfo(),
      ])
      if (!saved.http_enabled) {
        onError('该接口尚未启用转发调用')
        return
      }
      const example = buildProxyCallExample({
        item: saved,
        origin: window.location.origin,
        proxyPath: info.path,
        keyHeader: info.key_header,
      })
      await navigator.clipboard.writeText(example)
      setPublicationCopied(true)
    } catch (error) {
      onError(apiError(error) || '复制失败，请检查浏览器剪贴板权限')
    } finally {
      setPublicationCopying(false)
    }
  }

  return (
    <div ref={containerRef} className="scrollbar-none grid h-full min-h-0 overflow-x-auto overflow-y-hidden p-1" style={{ gridTemplateColumns: `minmax(250px, ${sizes[0]}fr) 4px minmax(680px, ${sizes[1]}fr)` }}>
      <aside className="flex min-h-0 min-w-0 flex-col overflow-hidden rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-elevated)] shadow-sm">
        <div className="border-b border-[var(--color-border)] p-3">
          <div className="mb-3 flex items-center justify-between"><div><h2 className="text-sm font-semibold">接口清单</h2><p className="text-[10px] text-[var(--color-text-tertiary)]">{interfaces.length} 个已纳管接口</p></div><Button size="sm" onClick={create}><CirclePlus size={13} />新建</Button></div>
          <label className="flex h-8 items-center gap-2 rounded-md border border-[var(--color-border)] bg-[var(--color-bg-base)] px-2.5">
            <Search size={14} className="text-[var(--color-text-tertiary)]" />
            <input value={search} onChange={event => setSearch(event.target.value)} placeholder="筛选名称、URL 或分组" className="min-w-0 flex-1 bg-transparent text-xs outline-none" />
          </label>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto p-2">
          {!filtered.length ? <EmptyList onCreate={create} /> : grouped.map(([group, items]) => (
            <div key={group || '__default'} className="mb-3">
              <div
                onDragOver={event => {
                  if (!draggingId || search.trim()) return
                  event.preventDefault()
                  event.dataTransfer.dropEffect = 'move'
                  setDropTarget({ group, index: 0 })
                }}
                onDrop={event => { event.preventDefault(); void moveInterface(group, 0) }}
                className={`flex items-center gap-1.5 rounded-md px-2 py-1.5 text-[11px] font-semibold uppercase tracking-wide transition-colors ${dropTarget?.group === group && dropTarget.index === 0 ? 'bg-emerald-50 text-emerald-700 ring-1 ring-inset ring-emerald-200' : 'text-[var(--color-text-tertiary)]'}`}
              >
                <Folder size={12} />{group || '默认分组'}<span className="ml-auto font-normal">{items.length}</span>
              </div>
              <div className="space-y-0.5">
                {items.map((item, index) => (
                  <div
                    key={item.id}
                    draggable={!search.trim() && !saving}
                    onDragStart={event => startInterfaceDrag(event, item)}
                    onDragEnd={() => { setDraggingId(null); setDropTarget(null) }}
                    onDragOver={event => {
                      if (!draggingId || draggingId === item.id || search.trim()) return
                      event.preventDefault()
                      event.dataTransfer.dropEffect = 'move'
                      const rect = event.currentTarget.getBoundingClientRect()
                      setDropTarget({ group, index: event.clientY < rect.top + rect.height / 2 ? index : index + 1 })
                    }}
                    onDrop={event => {
                      event.preventDefault()
                      event.stopPropagation()
                      const rect = event.currentTarget.getBoundingClientRect()
                      void moveInterface(group, event.clientY < rect.top + rect.height / 2 ? index : index + 1)
                    }}
                    className={`group relative flex min-h-10 w-full items-center rounded-md pr-1 transition-all ${draggingId === item.id ? 'opacity-45' : ''} ${selectedId === item.id ? 'bg-[var(--color-nav-light)]' : 'hover:bg-[var(--color-bg-hover)]'}`}
                  >
                    {dropTarget?.group === group && dropTarget.index === index && <span className="pointer-events-none absolute inset-x-1 top-0 h-0.5 rounded-full bg-emerald-500" />}
                    {dropTarget?.group === group && dropTarget.index === index + 1 && <span className="pointer-events-none absolute inset-x-1 bottom-0 h-0.5 rounded-full bg-emerald-500" />}
                    <span className={`flex h-8 w-5 shrink-0 items-center justify-center text-[var(--color-text-tertiary)] ${search.trim() ? 'cursor-not-allowed opacity-30' : 'cursor-grab active:cursor-grabbing'}`} title={search.trim() ? '清除筛选后可拖拽排序' : '拖拽调整顺序或移动分组'}><GripVertical size={12} /></span>
                    <button type="button" onClick={() => select(item)} className="flex min-w-0 flex-1 items-center gap-2 px-2.5 py-2 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-teal-500">
                      <span className={`w-12 shrink-0 rounded px-1.5 py-0.5 text-center text-[10px] font-bold ${methodTone[item.method] || methodTone.HEAD}`}>{item.method}</span>
                      <span className={`min-w-0 flex-1 truncate text-xs ${selectedId === item.id ? 'font-semibold text-[var(--color-nav-bg)]' : 'text-[var(--color-text-primary)]'}`}>{item.name}</span>
                      {item.open_enabled && <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-emerald-500" title="已加入开放清单" />}
                      <ChevronRight size={12} className="shrink-0 text-[var(--color-text-tertiary)] opacity-0 group-hover:opacity-100" />
                    </button>
                    <button
                      type="button"
                      onClick={() => setPublicationTarget(item)}
                      aria-label={`${item.name}：${item.http_enabled ? '查看已生成的转发' : '生成转发'}`}
                      title={item.http_enabled ? '查看转发与调用方式' : '由平台自动生成转发'}
                      className={`flex h-7 shrink-0 items-center gap-1 rounded px-2 text-[9px] font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500 ${item.http_enabled ? 'bg-emerald-100 text-emerald-700 hover:bg-emerald-200' : 'border border-dashed border-slate-300 bg-white/70 text-slate-500 hover:border-emerald-300 hover:text-emerald-700'}`}
                    >
                      <Share2 size={11} />{item.http_enabled ? '已转发' : '生成转发'}
                    </button>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
        <div className="grid shrink-0 grid-cols-3 gap-2 border-t border-[var(--color-border)] bg-white/60 p-3">
          <Button variant="outline" size="sm" onClick={() => setOpenInterfaces(true)}><Globe2 size={13} />开放接口</Button>
          <Button variant="outline" size="sm" onClick={() => setProxyKeys(true)}><KeyRound size={13} />调用方</Button>
          <Button variant="outline" size="sm" onClick={() => setSystemData(true)}><Database size={13} />系统数据</Button>
        </div>
      </aside>

      <div onPointerDown={startResize} role="separator" aria-orientation="vertical" aria-label="调整接口清单宽度" className="group flex cursor-col-resize items-center justify-center"><span className="flex h-12 w-3 items-center justify-center rounded-full border border-transparent text-[var(--color-text-tertiary)] transition-colors group-hover:border-teal-200 group-hover:bg-teal-50 group-hover:text-teal-600"><GripVertical size={12} /></span></div>

      <section className="flex min-h-0 min-w-0 flex-col overflow-hidden rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-elevated)] shadow-sm">
        <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-[var(--color-border)] px-4 py-3">
          <div className="flex min-w-[430px] flex-[1_1_430px] items-center gap-2">
            <input value={draft.name} onChange={event => patchDraft('name', event.target.value)} className="h-8 min-w-[180px] max-w-md flex-1 rounded-md border border-[var(--color-border)] bg-white px-3 text-sm font-semibold outline-none transition-colors placeholder:text-[var(--color-text-tertiary)] hover:border-[var(--color-border-hover)] focus:border-emerald-500 focus:ring-2 focus:ring-emerald-100" placeholder="接口名称" />
            <input list="api-hub-groups" value={draft.group_name} onChange={event => patchDraft('group_name', event.target.value)} className="h-8 w-36 shrink-0 rounded-md border border-[var(--color-border)] bg-white px-2.5 text-xs outline-none transition-colors hover:border-[var(--color-border-hover)] focus:border-emerald-500 focus:ring-2 focus:ring-emerald-100" placeholder="默认分组" title="输入或选择分组" />
            <datalist id="api-hub-groups">{groupNames.map(group => <option key={group} value={group} />)}</datalist>
            <Button size="sm" loading={saving} onClick={save}><Check size={14} />{draft.id ? '保存配置' : '保存接口'}</Button>
            {isDirty && <span className="shrink-0 rounded bg-amber-50 px-2 py-1 text-[10px] font-medium text-amber-700">未保存</span>}
          </div>
          <div className="ml-auto flex flex-wrap items-center justify-end gap-2">
            {draft.id && <Button variant="ghost" size="icon-sm" title="复制为新接口" onClick={() => { setSelectedId(null); setBaseline(emptyHubInterface()); setDraft({ ...structuredClone(draft), id: null, name: `${draft.name} 副本`, mcp_enabled: false, http_enabled: false, proxy_slug: '', proxy_query_keys: [], proxy_header_keys: [], proxy_body_enabled: false, proxy_body_keys: [] }); setResult(null); setResultFingerprint('') }}><Copy size={14} /></Button>}
            {draft.id && <Button variant="ghost" size="icon-sm" title="删除接口" className="text-[var(--color-danger)]" onClick={() => setDeleteOpen(true)}><Trash2 size={14} /></Button>}
            {draft.id && <Button variant="outline" size="sm" className="border-emerald-200 text-emerald-700 hover:bg-emerald-50" onClick={() => setPublicationTarget(structuredClone(baseline))}><Share2 size={14} />转发调用</Button>}
            {draft.id && draft.http_enabled && <Button variant="outline" size="sm" className="border-emerald-200 text-emerald-700 hover:bg-emerald-50" loading={publicationCopying} onClick={copyPublishedExample} aria-label={'复制“' + draft.name + '”的转发调用示例'}>{publicationCopied ? <Check size={14} /> : <Copy size={14} />}{publicationCopied ? '已复制' : '复制示例'}<span className="sr-only" aria-live="polite">{publicationCopied ? '转发调用示例复制成功' : ''}</span></Button>}
            <Button variant="outline" size="sm" className="border-emerald-200 text-emerald-700 hover:bg-emerald-50" onClick={showCallExample}><FileCode2 size={14} />调用示例</Button>
          </div>
        </div>

        <div className="shrink-0 p-4 pb-3">
          <div className="flex overflow-hidden rounded-md border border-[var(--color-border)] bg-[var(--color-bg-base)] focus-within:border-[var(--color-nav-bg)]">
            <select value={draft.method} onChange={event => patchDraft('method', event.target.value)} className="w-28 border-r border-[var(--color-border)] bg-transparent px-3 text-xs font-bold outline-none">
              {methods.map(method => <option key={method}>{method}</option>)}
            </select>
            <input value={draft.url} onChange={event => patchDraft('url', event.target.value)} className="h-10 min-w-0 flex-1 bg-transparent px-3 font-mono text-xs outline-none" placeholder="https://example.com/api/resource" />
            <button onClick={run} disabled={running} className={`relative m-1 flex min-w-[84px] items-center justify-center gap-1.5 overflow-hidden rounded bg-emerald-600 px-4 text-xs font-semibold text-white shadow-sm transition-all duration-200 hover:-translate-y-px hover:bg-emerald-700 active:translate-y-0 active:scale-[0.98] disabled:cursor-wait disabled:opacity-90 ${running ? 'ring-4 ring-emerald-100' : ''}`}>
              {running ? <><LoaderCircle size={14} className="animate-spin" />调用中…</> : <><Play size={13} />调用</>}
              {running && <span className="absolute inset-0 animate-pulse bg-white/10" />}
            </button>
          </div>
        </div>

        <div className="flex shrink-0 items-center justify-between border-b border-[var(--color-border)] px-4">
          <div className="flex gap-5">
            {(['params', 'headers', 'body', 'description'] as const).map(key => (
              <button key={key} onClick={() => setEditorTab(key)} className={`relative py-2.5 text-xs font-medium ${editorTab === key ? 'text-[var(--color-nav-bg)]' : 'text-[var(--color-text-secondary)]'}`}>
                {{ params: '查询参数', headers: '请求头', body: '请求体', description: '用途说明' }[key]}
                {editorTab === key && <span className="absolute inset-x-0 bottom-0 h-0.5 bg-[var(--color-nav-bg)]" />}
              </button>
            ))}
          </div>
          <div className="flex items-center gap-4">
            <Toggle label="注入 W3 登录态" value={draft.use_w3} onChange={value => patchDraft('use_w3', value)} />
            <Toggle label="开放清单" value={draft.open_enabled} onChange={value => patchDraft('open_enabled', value)} />
          </div>
        </div>

        <div className="min-h-[150px] shrink-0 overflow-y-auto border-b border-[var(--color-border)] p-4">
          {editorTab === 'params' && <KVEditor value={draft.query_params} onChange={value => patchDraft('query_params', value)} keyPlaceholder="参数名" valuePlaceholder="参数值" />}
          {editorTab === 'headers' && <KVEditor value={draft.headers} onChange={value => patchDraft('headers', value)} keyPlaceholder="Header" valuePlaceholder="值" />}
          {editorTab === 'body' && <BodyEditor draft={draft} patchDraft={patchDraft} />}
          {editorTab === 'description' && <textarea value={draft.description} onChange={event => patchDraft('description', event.target.value)} className="h-28 w-full resize-none rounded-md border border-[var(--color-border)] bg-[var(--color-bg-base)] p-3 text-xs outline-none focus:border-[var(--color-nav-bg)]" placeholder="描述接口用途、参数要求与返回结果，供 Agent 渐进式发现时理解此接口。" />}
        </div>

        <ResponsePanel result={result} stale={resultStale} loading={running} useW3={draft.use_w3} />
      </section>

      <ConfirmModal open={deleteOpen} onClose={() => setDeleteOpen(false)} onConfirm={remove} loading={saving} variant="danger" title={`删除“${draft.name}”？`} description="接口配置及其全部调用历史都会被删除，此操作不可撤销。" confirmText="删除接口" />
      <ConfirmModal open={Boolean(pendingNavigation)} onClose={() => setPendingNavigation(null)} onConfirm={discardAndNavigate} variant="danger" title="放弃未保存修改？" description="当前接口的未保存修改将丢失，且无法恢复。" confirmText="放弃并继续" />
      <Modal open={Boolean(callExampleDraft)} onClose={() => setCallExampleDraft(null)} title="调用示例" description="命令已根据当前编辑器草稿生成，可复制到终端运行或导入 Postman。" size="2xl" footer={<><Button variant="outline" onClick={() => setCallExampleDraft(null)}>关闭</Button><Button onClick={async () => { await navigator.clipboard.writeText(callExample); setCallExampleCopied(true) }}><Copy size={14} />{callExampleCopied ? '已复制' : '复制命令'}</Button></>}>
        <div className="space-y-4">
          <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-slate-950 p-4 text-xs leading-6 text-slate-100 shadow-inner">{callExample}</pre>
          {callExampleDraft?.use_w3 && (
            <label className={`flex items-start gap-3 rounded-lg border px-3.5 py-3 transition-colors ${includeLogin ? 'border-amber-200 bg-amber-50' : 'border-[var(--color-border)] bg-slate-50'}`}>
              <input type="checkbox" checked={includeLogin} disabled={cookieLoading} onChange={event => void toggleExampleLogin(event.target.checked)} className="mt-0.5 h-4 w-4 accent-emerald-600" />
              <span className="min-w-0 text-xs leading-5 text-slate-600"><b className="font-semibold text-slate-800">包含当前 W3 登录 Cookie</b><br />用于在终端或 Postman 复用登录态；Cookie 属于敏感信息且会过期，请勿转发给无关人员。</span>
              {cookieLoading && <LoaderCircle size={15} className="mt-0.5 shrink-0 animate-spin text-emerald-600" />}
            </label>
          )}
          {cookieMessage && <div className="rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800">{cookieMessage}</div>}
        </div>
      </Modal>
      <OpenInterfacesModal open={openInterfaces} onClose={() => setOpenInterfaces(false)} interfaces={interfaces} reload={reloadOpenState} onError={onError} />
      <HttpPublicationModal open={Boolean(publicationTarget)} onClose={() => setPublicationTarget(null)} item={publicationTarget} reload={reloadPublication} onError={onError} />
      <ProxyKeysModal open={proxyKeys} onClose={() => setProxyKeys(false)} interfaces={interfaces} onError={onError} />
      <SystemDataModal open={systemData} onClose={() => setSystemData(false)} interfaces={interfaces} reload={reload} onError={onError} />
    </div>
  )
}

function EmptyList({ onCreate }: { onCreate: () => void }) {
  return <div className="flex flex-col items-center px-5 py-14 text-center"><Braces size={28} className="mb-3 text-[var(--color-text-tertiary)]" /><p className="text-xs text-[var(--color-text-secondary)]">还没有匹配的接口</p><button onClick={onCreate} className="mt-2 text-xs font-medium text-[var(--color-nav-bg)]">新建一个接口</button></div>
}

function Toggle({ label, value, onChange }: { label: string; value: boolean; onChange: (value: boolean) => void }) {
  return <label className="flex cursor-pointer items-center gap-1.5 text-[11px] text-[var(--color-text-secondary)]"><button type="button" aria-label={label} aria-pressed={value} onClick={() => onChange(!value)} className={`relative h-4 w-7 rounded-full transition-colors ${value ? 'bg-[var(--color-nav-bg)]' : 'bg-[var(--color-border-hover)]'}`}><span className={`absolute top-0.5 h-3 w-3 rounded-full bg-white transition-transform ${value ? 'translate-x-3.5' : 'translate-x-0.5'}`} /></button>{label}</label>
}

function KVEditor({ value, onChange, keyPlaceholder, valuePlaceholder }: { value: KV[]; onChange: (value: KV[]) => void; keyPlaceholder: string; valuePlaceholder: string }) {
  const rows = value.length ? value : [{ key: '', value: '' }]
  const update = (index: number, key: keyof KV, text: string) => onChange(rows.map((row, i) => i === index ? { ...row, [key]: text } : row))
  return <div className="space-y-2">{rows.map((row, index) => <div key={index} className="flex items-center gap-2"><input value={row.key} onChange={event => update(index, 'key', event.target.value)} className="h-8 w-2/5 rounded-md border border-[var(--color-border)] bg-[var(--color-bg-base)] px-2.5 font-mono text-xs outline-none" placeholder={keyPlaceholder} /><input value={row.value} onChange={event => update(index, 'value', event.target.value)} className="h-8 min-w-0 flex-1 rounded-md border border-[var(--color-border)] bg-[var(--color-bg-base)] px-2.5 font-mono text-xs outline-none" placeholder={valuePlaceholder} /><button onClick={() => onChange(rows.filter((_, i) => i !== index))} className="text-[var(--color-text-tertiary)] hover:text-[var(--color-danger)]"><X size={14} /></button></div>)}<button onClick={() => onChange([...rows, { key: '', value: '' }])} className="flex items-center gap-1 text-xs text-[var(--color-nav-bg)]"><Plus size={13} />添加一行</button></div>
}

function BodyEditor({ draft, patchDraft }: { draft: HubInterface; patchDraft: <K extends keyof HubInterface>(key: K, value: HubInterface[K]) => void }) {
  return <div><div className="mb-2 flex gap-1">{(['none', 'json', 'form', 'raw'] as const).map(type => <button key={type} onClick={() => patchDraft('body_type', type)} className={`rounded px-2.5 py-1 text-[11px] ${draft.body_type === type ? 'bg-[var(--color-nav-light)] font-semibold text-[var(--color-nav-bg)]' : 'text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)]'}`}>{type.toUpperCase()}</button>)}</div>{draft.body_type === 'none' ? <div className="rounded-md border border-dashed border-[var(--color-border)] py-9 text-center text-xs text-[var(--color-text-tertiary)]">当前请求不发送 Body</div> : <textarea value={draft.body_content} onChange={event => patchDraft('body_content', event.target.value)} className="h-28 w-full resize-none rounded-md border border-[var(--color-border)] bg-[var(--color-bg-base)] p-3 font-mono text-xs outline-none focus:border-[var(--color-nav-bg)]" placeholder={draft.body_type === 'json' ? '{\n  "key": "value"\n}' : draft.body_type === 'form' ? 'key=value\nother=value' : '原始请求内容'} />}</div>
}

function ResponsePanel({ result, stale, loading, useW3 }: { result: RunResult | null; stale: boolean; loading: boolean; useW3: boolean }) {
  const response = useMemo(() => formatResponseBody(result?.response_body ?? ''), [result?.response_body])
  const copyText = response.text || result?.error || ''
  const [copyFeedback, setCopyFeedback] = useState<{ text: string; status: 'copied' | 'failed' } | null>(null)
  const copyResetRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const copyStatus = copyFeedback?.text === copyText ? copyFeedback.status : null

  useEffect(() => () => {
    if (copyResetRef.current) clearTimeout(copyResetRef.current)
  }, [])

  const copyResponse = async () => {
    if (!copyText) return
    if (copyResetRef.current) clearTimeout(copyResetRef.current)
    try {
      await navigator.clipboard.writeText(copyText)
      setCopyFeedback({ text: copyText, status: 'copied' })
    } catch {
      setCopyFeedback({ text: copyText, status: 'failed' })
    }
    copyResetRef.current = setTimeout(() => setCopyFeedback(null), 1800)
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex h-10 shrink-0 items-center gap-3 border-b border-[var(--color-border)] px-4">
        <span className="text-xs font-semibold text-[var(--color-text-primary)]">响应</span>
        {loading ? (
          <span className="flex items-center gap-1.5 text-[11px] font-medium text-emerald-700"><LoaderCircle size={12} className="animate-spin" />请求处理中</span>
        ) : result && (
          <>
            <span className={`rounded px-2 py-0.5 text-[11px] font-semibold ${stale ? 'bg-slate-100 text-slate-500' : result.status_code && result.status_code < 400 ? 'bg-[var(--color-success-bg)] text-[var(--color-success)]' : 'bg-[var(--color-danger-bg)] text-[var(--color-danger)]'}`}>{result.status_code ?? 'ERR'}</span>
            <span className="text-[11px] text-[var(--color-text-tertiary)]">{result.elapsed_ms ?? '—'} ms</span>
            {response.isJson && <span className="rounded bg-emerald-50 px-1.5 py-0.5 font-mono text-[10px] font-semibold text-emerald-700">JSON</span>}
            {stale && <span className="text-[11px] font-medium text-amber-700">请求已修改，此结果已过期</span>}
            {result.relogin && <span className="text-[11px] text-[var(--color-warning)]">已自动重登</span>}
          </>
        )}
        {result && !loading && (
          <button
            type="button"
            onClick={() => void copyResponse()}
            disabled={!copyText}
            title="复制响应内容"
            className={`ml-auto flex h-7 shrink-0 items-center gap-1.5 rounded-md border px-2.5 text-[11px] font-medium transition-all duration-200 active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-40 ${copyStatus === 'copied' ? 'border-emerald-200 bg-emerald-50 text-emerald-700' : copyStatus === 'failed' ? 'border-red-200 bg-red-50 text-red-600' : 'border-[var(--color-border)] bg-white text-[var(--color-text-secondary)] hover:border-emerald-300 hover:bg-emerald-50 hover:text-emerald-700'}`}
          >
            {copyStatus === 'copied' ? <Check size={12} /> : <Copy size={12} />}
            {copyStatus === 'copied' ? '已复制' : copyStatus === 'failed' ? '复制失败' : '复制'}
          </button>
        )}
      </div>

      {loading ? (
        <div className="flex flex-1 flex-col items-center justify-center px-8 text-center"><div className="relative mb-5 flex h-12 w-12 items-center justify-center rounded-full bg-emerald-50"><span className="absolute inset-0 animate-ping rounded-full bg-emerald-100 opacity-70" /><LoaderCircle size={22} className="relative animate-spin text-emerald-600" /></div><div className="text-sm font-semibold text-slate-700">正在调用接口</div><div className="mt-1.5 text-xs text-slate-500">{useW3 ? '正在注入 W3 登录态并等待接口响应…' : '正在连接目标服务并等待响应…'}</div><div className="mt-5 w-full max-w-sm space-y-2"><span className="block h-2 animate-pulse rounded bg-slate-200" /><span className="block h-2 w-4/5 animate-pulse rounded bg-slate-100" /><span className="block h-2 w-3/5 animate-pulse rounded bg-slate-100" /></div></div>
      ) : !result ? (
        <div className="flex flex-1 items-center justify-center text-xs text-[var(--color-text-tertiary)]"><Send size={18} className="mr-2 opacity-50" />点击“调用”查看响应</div>
      ) : (
        <div className={`min-h-0 flex-1 animate-fade-in overflow-auto bg-slate-50/60 ${stale ? 'opacity-60' : ''}`}>
          {result.error && <div className="m-4 mb-3 rounded-md bg-[var(--color-danger-bg)] px-3 py-2 text-xs text-[var(--color-danger)]">{result.error}</div>}
          {response.isJson ? <JsonResponse value={response.text} /> : <pre className="whitespace-pre-wrap break-words p-4 font-mono text-xs leading-6 text-[var(--color-text-primary)]">{response.text || '(空响应体)'}</pre>}
        </div>
      )}
    </div>
  )
}

function JsonResponse({ value }: { value: string }) {
  return (
    <div className="min-w-max py-3 font-mono text-xs leading-6">
      {value.split('\n').map((line, index) => (
        <div key={index} className="grid grid-cols-[3rem_minmax(0,1fr)] hover:bg-emerald-50/60">
          <span className="select-none border-r border-slate-200 pr-3 text-right text-[10px] text-slate-400">{index + 1}</span>
          <code className="whitespace-pre px-4">{highlightJsonLine(line)}</code>
        </div>
      ))}
    </div>
  )
}

function highlightJsonLine(line: string) {
  const tokenPattern = /("(?:\\u[\da-fA-F]{4}|\\[^u]|[^\\"])*"\s*:)|("(?:\\u[\da-fA-F]{4}|\\[^u]|[^\\"])*")|(\btrue\b|\bfalse\b)|(\bnull\b)|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)/g
  const parts = []
  let lastIndex = 0

  for (const match of line.matchAll(tokenPattern)) {
    const index = match.index
    if (index > lastIndex) parts.push(line.slice(lastIndex, index))
    const token = match[0]
    const tone = match[1]
      ? 'text-emerald-700'
      : match[2]
        ? 'text-sky-700'
        : match[3]
          ? 'font-semibold text-violet-700'
          : match[4]
            ? 'italic text-slate-500'
            : 'text-amber-700'
    parts.push(<span key={`${index}-${token}`} className={tone}>{token}</span>)
    lastIndex = index + token.length
  }
  if (lastIndex < line.length) parts.push(line.slice(lastIndex))
  return parts
}

function formatResponseBody(body: string) {
  if (!body) return { text: '', isJson: false }
  try {
    return { text: JSON.stringify(JSON.parse(body), null, 2), isJson: true }
  } catch {
    return { text: body, isJson: false }
  }
}

function requestFingerprint(item: HubInterface) {
  return JSON.stringify({
    method: item.method,
    url: item.url,
    query_params: item.query_params,
    headers: item.headers,
    body_type: item.body_type,
    body_content: item.body_content,
    use_w3: item.use_w3,
  })
}

function draftFingerprint(item: HubInterface) {
  return JSON.stringify({
    name: item.name,
    description: item.description,
    group_name: item.group_name,
    ...JSON.parse(requestFingerprint(item)),
    mcp_enabled: item.mcp_enabled,
    open_enabled: item.open_enabled,
    http_enabled: item.http_enabled,
    proxy_slug: item.proxy_slug,
    proxy_query_keys: item.proxy_query_keys,
    proxy_header_keys: item.proxy_header_keys,
    proxy_body_enabled: item.proxy_body_enabled,
    proxy_body_keys: item.proxy_body_keys,
  })
}

function shellQuote(value: string) {
  return `'${value.replaceAll("'", "'\\''")}'`
}

function buildCallExample(draft: HubInterface, cookie: string) {
  const url = new URL(draft.url)
  draft.query_params.filter(item => item.key.trim()).forEach(item => url.searchParams.append(item.key.trim(), item.value))
  const method = methods.includes(draft.method.toUpperCase()) ? draft.method.toUpperCase() : 'GET'
  const headers = draft.headers.filter(item => item.key.trim()).map(item => ({ key: item.key.trim(), value: item.value }))
  const hasContentType = headers.some(item => item.key.toLowerCase() === 'content-type')
  let body = ''
  if (draft.body_type === 'json' && draft.body_content.trim()) {
    if (!hasContentType) headers.push({ key: 'Content-Type', value: 'application/json; charset=utf-8' })
    body = draft.body_content
  } else if (draft.body_type === 'form' && draft.body_content.trim()) {
    if (!hasContentType) headers.push({ key: 'Content-Type', value: 'application/x-www-form-urlencoded' })
    const form = new URLSearchParams()
    draft.body_content.split('\n').map(line => line.trim()).filter(line => line && !line.startsWith('#')).forEach(line => {
      const separator = line.indexOf('=')
      form.append(separator < 0 ? line : line.slice(0, separator), separator < 0 ? '' : line.slice(separator + 1))
    })
    body = form.toString()
  } else if (draft.body_type === 'raw' && draft.body_content) {
    body = draft.body_content
  }

  const pieces = [`curl -X ${method} ${shellQuote(url.toString())}`, '  -k']
  headers.forEach(item => pieces.push(`  -H ${shellQuote(`${item.key}: ${item.value}`)}`))
  if (cookie) pieces.push(`  -b ${shellQuote(cookie)}`)
  if (body) pieces.push(`  --data-raw ${shellQuote(body)}`)
  return pieces.join(' \\\n')
}
