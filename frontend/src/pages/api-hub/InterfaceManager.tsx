import { useCallback, useMemo, useRef, useState } from 'react'
import {
  Braces, Check, ChevronRight, CirclePlus, Copy, FileCode2, Folder, Play,
  Plus, Search, Send, Trash2, X, Database, Globe2, GripVertical, KeyRound, Share2,
} from 'lucide-react'
import { apiError, apiHub, emptyHubInterface, type HubInterface, type KV, type RunResult } from '@/api/apiHub'
import { Button } from '@/components/ui/Button'
import { ConfirmModal, Modal } from '@/components/ui/Modal'
import {
  HttpPublicationModal, OpenInterfacesModal, ProxyKeysModal, SystemDataModal,
} from './InterfaceDataModals'

interface Props {
  interfaces: HubInterface[]
  reload: () => Promise<HubInterface[]>
  onError: (message: string) => void
}

const methods = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD']
const methodTone: Record<string, string> = {
  GET: 'text-blue-600 bg-blue-50', POST: 'text-emerald-700 bg-emerald-50',
  PUT: 'text-amber-700 bg-amber-50', PATCH: 'text-violet-700 bg-violet-50',
  DELETE: 'text-red-600 bg-red-50', HEAD: 'text-slate-600 bg-slate-100',
}

export default function InterfaceManager({ interfaces, reload, onError }: Props) {
  const [selectedId, setSelectedId] = useState<number | null>(interfaces[0]?.id ?? null)
  const [draft, setDraft] = useState<HubInterface>(() => interfaces[0] ? structuredClone(interfaces[0]) : emptyHubInterface())
  const [search, setSearch] = useState('')
  const [editorTab, setEditorTab] = useState<'params' | 'headers' | 'body' | 'description'>('params')
  const [saving, setSaving] = useState(false)
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState<RunResult | null>(null)
  const [deleteOpen, setDeleteOpen] = useState(false)
  const [curl, setCurl] = useState('')
  const [openInterfaces, setOpenInterfaces] = useState(false)
  const [httpPublication, setHttpPublication] = useState(false)
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

  const select = (item: HubInterface) => {
    setSelectedId(item.id)
    setDraft(structuredClone(item))
    setResult(null)
  }
  const create = () => {
    setSelectedId(null)
    setDraft(emptyHubInterface())
    setResult(null)
  }
  const patchDraft = <K extends keyof HubInterface>(key: K, value: HubInterface[K]) => setDraft(current => ({ ...current, [key]: value }))

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
      await reload()
      return saved
    } catch (error) {
      onError(apiError(error)); return null
    } finally { setSaving(false) }
  }

  const reloadSelected = async () => {
    const items = await reload()
    const refreshed = items.find(item => item.id === draft.id)
    if (refreshed) setDraft(structuredClone(refreshed))
    return items
  }

  const run = async () => {
    setRunning(true)
    try {
      const saved = await save()
      if (!saved?.id) return
      setResult(await apiHub.run(saved.id))
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
      setResult(null)
      setDeleteOpen(false)
    } catch (error) { onError(apiError(error)) }
    finally { setSaving(false) }
  }

  const showCurl = async () => {
    try {
      const url = new URL(draft.url || 'http://example.com')
      draft.query_params.filter(item => item.key).forEach(item => url.searchParams.set(item.key, item.value))
      const pieces = [`curl -X ${draft.method}`, `'${url.toString()}'`]
      draft.headers.filter(item => item.key).forEach(item => pieces.push(`-H '${item.key}: ${item.value.replaceAll("'", "'\\''")}'`))
      if (draft.use_w3) {
        const cookie = await apiHub.cookieHeader().catch(() => ({ cookie: '', count: 0 }))
        if (cookie.cookie) pieces.push(`-H 'Cookie: ${cookie.cookie.replaceAll("'", "'\\''")}'`)
      }
      if (draft.body_type !== 'none' && draft.body_content) pieces.push(`--data-raw '${draft.body_content.replaceAll("'", "'\\''")}'`)
      setCurl(pieces.join(' \\\n  '))
    } catch {
      onError('请先填写有效的绝对 URL，再导出 cURL')
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
              <div className="flex items-center gap-1.5 px-2 py-1.5 text-[11px] font-semibold uppercase tracking-wide text-[var(--color-text-tertiary)]">
                <Folder size={12} />{group || '默认分组'}<span className="ml-auto font-normal">{items.length}</span>
              </div>
              <div className="space-y-0.5">
                {items.map(item => (
                  <button key={item.id} onClick={() => select(item)} className={`group flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left ${selectedId === item.id ? 'bg-[var(--color-nav-light)]' : 'hover:bg-[var(--color-bg-hover)]'}`}>
                    <span className={`w-12 shrink-0 rounded px-1.5 py-0.5 text-center text-[10px] font-bold ${methodTone[item.method] || methodTone.HEAD}`}>{item.method}</span>
                    <span className={`min-w-0 flex-1 truncate text-xs ${selectedId === item.id ? 'font-semibold text-[var(--color-nav-bg)]' : 'text-[var(--color-text-primary)]'}`}>{item.name}</span>
                    {item.http_enabled && <span className="rounded bg-violet-50 px-1.5 py-0.5 text-[8px] font-bold text-violet-700" title="已发布普通 HTTP 接口">HTTP</span>}
                    {item.open_enabled && <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" title="已加入开放清单" />}
                    <ChevronRight size={12} className="text-[var(--color-text-tertiary)] opacity-0 group-hover:opacity-100" />
                  </button>
                ))}
              </div>
            </div>
          ))}
        </div>
        <div className="grid shrink-0 grid-cols-3 gap-2 border-t border-[var(--color-border)] bg-white/60 p-3">
          <Button variant="outline" size="sm" onClick={() => setOpenInterfaces(true)}><Globe2 size={13} />开放接口</Button>
          <Button variant="outline" size="sm" onClick={() => setProxyKeys(true)}><KeyRound size={13} />HTTP密钥</Button>
          <Button variant="outline" size="sm" onClick={() => setSystemData(true)}><Database size={13} />系统数据</Button>
        </div>
      </aside>

      <div onPointerDown={startResize} role="separator" aria-orientation="vertical" aria-label="调整接口清单宽度" className="group flex cursor-col-resize items-center justify-center"><span className="flex h-12 w-3 items-center justify-center rounded-full border border-transparent text-[var(--color-text-tertiary)] transition-colors group-hover:border-teal-200 group-hover:bg-teal-50 group-hover:text-teal-600"><GripVertical size={12} /></span></div>

      <section className="flex min-h-0 min-w-0 flex-col overflow-hidden rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-elevated)] shadow-sm">
        <div className="flex shrink-0 items-center gap-3 border-b border-[var(--color-border)] px-4 py-3">
          <input value={draft.name} onChange={event => patchDraft('name', event.target.value)} className="min-w-0 flex-1 bg-transparent text-base font-semibold outline-none placeholder:text-[var(--color-text-tertiary)]" placeholder="接口名称" />
          <input list="api-hub-groups" value={draft.group_name} onChange={event => patchDraft('group_name', event.target.value)} className="h-8 w-36 rounded-md border border-[var(--color-border)] bg-[var(--color-bg-base)] px-2.5 text-xs outline-none" placeholder="默认分组" />
          <datalist id="api-hub-groups">{groupNames.map(group => <option key={group} value={group} />)}</datalist>
          {draft.id && <Button variant="ghost" size="icon-sm" title="复制为新接口" onClick={() => { setSelectedId(null); setDraft({ ...structuredClone(draft), id: null, name: `${draft.name} 副本`, http_enabled: false, proxy_slug: '' }) }}><Copy size={14} /></Button>}
          {draft.id && <Button variant="ghost" size="icon-sm" title="删除接口" className="text-[var(--color-danger)]" onClick={() => setDeleteOpen(true)}><Trash2 size={14} /></Button>}
          {draft.id && <Button variant="outline" size="sm" onClick={() => setHttpPublication(true)}><Share2 size={14} />HTTP发布</Button>}
          <Button variant="outline" size="sm" onClick={showCurl}><FileCode2 size={14} />cURL</Button>
          <Button variant="outline" size="sm" loading={saving} onClick={save}><Check size={14} />保存</Button>
          <Button size="sm" loading={running} onClick={run}><Send size={14} />发送</Button>
        </div>

        <div className="shrink-0 p-4 pb-3">
          <div className="flex overflow-hidden rounded-md border border-[var(--color-border)] bg-[var(--color-bg-base)] focus-within:border-[var(--color-nav-bg)]">
            <select value={draft.method} onChange={event => patchDraft('method', event.target.value)} className="w-28 border-r border-[var(--color-border)] bg-transparent px-3 text-xs font-bold outline-none">
              {methods.map(method => <option key={method}>{method}</option>)}
            </select>
            <input value={draft.url} onChange={event => patchDraft('url', event.target.value)} className="h-10 min-w-0 flex-1 bg-transparent px-3 font-mono text-xs outline-none" placeholder="https://example.com/api/resource" />
            <button onClick={run} disabled={running} className="m-1 flex items-center gap-1.5 rounded bg-[var(--color-nav-bg)] px-4 text-xs font-medium text-white hover:opacity-90 disabled:opacity-50"><Play size={13} />调用</button>
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
            <Toggle label="W3 登录态" value={draft.use_w3} onChange={value => patchDraft('use_w3', value)} />
            <Toggle label="独立 MCP" value={draft.mcp_enabled} onChange={value => patchDraft('mcp_enabled', value)} />
            <Toggle label="开放清单" value={draft.open_enabled} onChange={value => patchDraft('open_enabled', value)} />
          </div>
        </div>

        <div className="min-h-[150px] shrink-0 overflow-y-auto border-b border-[var(--color-border)] p-4">
          {editorTab === 'params' && <KVEditor value={draft.query_params} onChange={value => patchDraft('query_params', value)} keyPlaceholder="参数名" valuePlaceholder="参数值" />}
          {editorTab === 'headers' && <KVEditor value={draft.headers} onChange={value => patchDraft('headers', value)} keyPlaceholder="Header" valuePlaceholder="值" />}
          {editorTab === 'body' && <BodyEditor draft={draft} patchDraft={patchDraft} />}
          {editorTab === 'description' && <textarea value={draft.description} onChange={event => patchDraft('description', event.target.value)} className="h-28 w-full resize-none rounded-md border border-[var(--color-border)] bg-[var(--color-bg-base)] p-3 text-xs outline-none focus:border-[var(--color-nav-bg)]" placeholder="描述接口用途、参数要求与返回结果，供 Agent 渐进式发现时理解此接口。" />}
        </div>

        <ResponsePanel result={result} />
      </section>

      <ConfirmModal open={deleteOpen} onClose={() => setDeleteOpen(false)} onConfirm={remove} loading={saving} variant="danger" title={`删除“${draft.name}”？`} description="接口配置及其全部调用历史都会被删除，此操作不可撤销。" confirmText="删除接口" />
      <Modal open={Boolean(curl)} onClose={() => setCurl('')} title="导出 cURL" description={draft.use_w3 ? '已尽力附带当前 W3 Cookie，请勿将命令分享给无关人员。' : '命令已根据当前编辑器内容生成。'} size="2xl" footer={<Button onClick={() => navigator.clipboard.writeText(curl)}><Copy size={14} />复制命令</Button>}>
        <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-all rounded-md bg-[#111827] p-4 text-xs leading-6 text-slate-100">{curl}</pre>
      </Modal>
      <OpenInterfacesModal open={openInterfaces} onClose={() => setOpenInterfaces(false)} interfaces={interfaces} reload={reload} onError={onError} />
      <HttpPublicationModal open={httpPublication} onClose={() => setHttpPublication(false)} item={draft.id ? draft : null} reload={reloadSelected} onError={onError} onManageKeys={() => { setHttpPublication(false); setProxyKeys(true) }} />
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

function ResponsePanel({ result }: { result: RunResult | null }) {
  const pretty = useMemo(() => {
    if (!result?.response_body) return ''
    try { return JSON.stringify(JSON.parse(result.response_body), null, 2) } catch { return result.response_body }
  }, [result])
  return <div className="flex min-h-0 flex-1 flex-col"><div className="flex h-10 shrink-0 items-center gap-3 border-b border-[var(--color-border)] px-4"><span className="text-xs font-semibold text-[var(--color-text-primary)]">响应</span>{result && <><span className={`rounded px-2 py-0.5 text-[11px] font-semibold ${result.status_code && result.status_code < 400 ? 'bg-[var(--color-success-bg)] text-[var(--color-success)]' : 'bg-[var(--color-danger-bg)] text-[var(--color-danger)]'}`}>{result.status_code ?? 'ERR'}</span><span className="text-[11px] text-[var(--color-text-tertiary)]">{result.elapsed_ms ?? '—'} ms</span>{result.relogin && <span className="text-[11px] text-[var(--color-warning)]">已自动重登</span>}</>}</div>{!result ? <div className="flex flex-1 items-center justify-center text-xs text-[var(--color-text-tertiary)]"><Send size={18} className="mr-2 opacity-50" />点击“发送”查看响应</div> : <div className="min-h-0 flex-1 overflow-auto p-4">{result.error && <div className="mb-3 rounded-md bg-[var(--color-danger-bg)] px-3 py-2 text-xs text-[var(--color-danger)]">{result.error}</div>}<pre className="whitespace-pre-wrap break-words font-mono text-xs leading-5 text-[var(--color-text-primary)]">{pretty || '(空响应体)'}</pre></div>}</div>
}
