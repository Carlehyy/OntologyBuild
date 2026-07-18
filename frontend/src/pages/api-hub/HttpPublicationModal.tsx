import { useEffect, useState } from 'react'
import {
  CheckCircle2, Code2, Copy, RefreshCw, Share2, Sparkles,
} from 'lucide-react'
import {
  apiError, apiHub, type ForwardingPackage, type HubInterface,
} from '@/api/apiHub'
import { Button } from '@/components/ui/Button'
import { Modal } from '@/components/ui/Modal'

interface Props {
  open: boolean
  onClose: () => void
  item: HubInterface | null
  reload: () => Promise<HubInterface[]>
  onError: (message: string) => void
}

export function HttpPublicationModal({ open, onClose, item, reload, onError }: Props) {
  const [current, setCurrent] = useState<HubInterface | null>(item)
  const [callPackage, setCallPackage] = useState<ForwardingPackage | null>(null)
  const [tab, setTab] = useState<'curl' | 'python' | 'javascript'>('curl')
  const [busy, setBusy] = useState(false)
  const [copied, setCopied] = useState('')
  const [proxyPath, setProxyPath] = useState('/proxy')

  useEffect(() => {
    if (!open || !item) return
    setCurrent(item)
    setCallPackage(null)
    setTab('curl')
    setCopied('')
    apiHub.proxyInfo().then(info => setProxyPath(info.path)).catch(error => onError(apiError(error)))
  }, [item, onError, open])

  if (!item || !current) return null

  const inferredCount = current.proxy_query_keys.length
    + current.proxy_header_keys.length
    + current.proxy_body_keys.length
  const publicUrl = current.http_enabled && current.proxy_slug
    ? `${window.location.origin}${proxyPath}/${current.proxy_slug}`
    : ''

  const autoPublish = async (createPackage: boolean) => {
    if (!current.id) return
    setBusy(true)
    try {
      const published = await apiHub.autoHttpPublication(current.id)
      setCurrent(published)
      await reload()
      if (createPackage) {
        setCallPackage(await apiHub.createForwardingPackage(current.id))
        setTab('curl')
      }
    } catch (error) { onError(apiError(error)) }
    finally { setBusy(false) }
  }

  const disable = async () => {
    if (!current.id) return
    setBusy(true)
    try {
      const disabled = await apiHub.setHttpPublication(current.id, {
        enabled: false,
        slug: current.proxy_slug,
        query_keys: current.proxy_query_keys,
        header_keys: current.proxy_header_keys,
        body_enabled: current.proxy_body_enabled,
        body_keys: current.proxy_body_keys,
      })
      setCurrent(disabled)
      setCallPackage(null)
      await reload()
    } catch (error) { onError(apiError(error)) }
    finally { setBusy(false) }
  }

  const copy = async (value: string, key: string) => {
    try {
      await navigator.clipboard.writeText(value)
      setCopied(key)
    } catch { onError('复制失败，请手动选择代码复制') }
  }

  const code = callPackage ? forwardingCode(callPackage, tab) : ''

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={`转发调用 · ${current.name}`}
      description="平台自动识别可修改的数据、保管敏感信息，并生成别人拿来就能用的调用代码。"
      size="3xl"
      headerIcon={<Share2 size={19} className="text-violet-700" />}
      footer={callPackage ? (
        <>
          <Button variant="outline" onClick={onClose}>完成</Button>
          <Button onClick={() => void copy(code, 'code')}><Copy size={14} />{copied === 'code' ? '已复制' : '复制当前代码'}</Button>
        </>
      ) : (
        <>
          {current.http_enabled && <Button variant="ghost" onClick={() => void disable()} disabled={busy} className="mr-auto text-slate-500">停止转发</Button>}
          {current.http_enabled && <Button variant="outline" onClick={() => void autoPublish(false)} loading={busy}><RefreshCw size={14} />重新识别</Button>}
          <Button onClick={() => void autoPublish(true)} loading={busy}><Sparkles size={14} />{current.http_enabled ? '生成新的调用包' : '发布并生成调用包'}</Button>
        </>
      )}
    >
      <div className="max-h-[66vh] space-y-5 overflow-y-auto pr-1">
        <section className={`flex items-center justify-between gap-5 rounded-xl border px-4 py-4 ${current.http_enabled ? 'border-emerald-200 bg-emerald-50/70' : 'border-violet-100 bg-violet-50/60'}`}>
          <div className="flex min-w-0 items-start gap-3">
            <div className={`mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg ${current.http_enabled ? 'bg-emerald-100 text-emerald-700' : 'bg-violet-100 text-violet-700'}`}>
              {current.http_enabled ? <CheckCircle2 size={18} /> : <Sparkles size={18} />}
            </div>
            <div className="min-w-0">
              <div className="text-sm font-semibold text-slate-900">{current.http_enabled ? '转发已经可以使用' : '平台将自动完成全部配置'}</div>
              <p className="mt-1 text-xs leading-5 text-slate-600">{current.http_enabled ? `已识别 ${inferredCount} 个调用方可修改的数据；认证信息与固定参数继续由平台保管。` : '无需填写地址、参数位置或授权规则。平台会基于当前已保存请求生成安全配置。'}</p>
            </div>
          </div>
          <span className={`shrink-0 rounded-full px-2.5 py-1 text-[10px] font-semibold ${current.http_enabled ? 'bg-emerald-100 text-emerald-800' : 'bg-white text-violet-700'}`}>{current.http_enabled ? '已转发' : '待生成'}</span>
        </section>

        {!callPackage && (
          <section className="grid grid-cols-1 gap-3 sm:grid-cols-3" aria-label="平台自动处理的步骤">
            <AutoStep index="1" title="识别业务数据" text="从当前请求中找出调用方需要修改的内容" />
            <AutoStep index="2" title="保护敏感配置" text="登录态、密钥和敏感字段只保留在平台" />
            <AutoStep index="3" title="生成可用代码" text="自动创建专属凭证和完整调用示例" />
          </section>
        )}

        {current.http_enabled && !callPackage && (
          <section className="rounded-xl border border-[var(--color-border)] bg-white p-4">
            <div className="flex items-center justify-between gap-4">
              <div><div className="text-xs font-semibold text-slate-800">当前调用地址</div><div className="mt-1 text-[11px] text-slate-500">调用方无需知道真实接口和平台登录态。</div></div>
              <button type="button" onClick={() => void copy(publicUrl, 'url')} className="rounded-md px-2 py-1 text-xs font-medium text-teal-700 hover:bg-teal-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500">{copied === 'url' ? '已复制' : '复制地址'}</button>
            </div>
            <code className="mt-3 block break-all rounded-lg bg-slate-950 px-3 py-2.5 text-[11px] text-slate-100">{publicUrl}</code>
          </section>
        )}

        {callPackage && (
          <section className="space-y-4">
            <div className="rounded-xl border border-emerald-200 bg-emerald-50/70 p-4">
              <div className="flex items-start gap-3"><CheckCircle2 size={20} className="mt-0.5 shrink-0 text-emerald-700" /><div><div className="text-sm font-semibold text-emerald-900">调用包已生成</div><p className="mt-1 text-xs leading-5 text-emerald-800">这份凭证只允许调用“{current.name}”。把下面代码发给调用方，对方修改业务数据后即可运行。</p></div></div>
            </div>

            <div className="rounded-xl border border-[var(--color-border)] bg-white p-4">
              <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
                <div className="flex items-center gap-2"><Code2 size={15} className="text-violet-700" /><span className="text-xs font-semibold text-slate-800">可直接使用的调用代码</span></div>
                <div className="flex rounded-lg bg-slate-100 p-1">
                  {(['curl', 'python', 'javascript'] as const).map(value => (
                    <button key={value} type="button" onClick={() => { setTab(value); setCopied('') }} className={`rounded-md px-3 py-1 text-[10px] font-semibold transition-colors ${tab === value ? 'bg-white text-slate-900 shadow-sm' : 'text-slate-500 hover:text-slate-800'}`}>{value === 'curl' ? 'cURL' : value === 'python' ? 'Python' : 'JavaScript'}</button>
                  ))}
                </div>
              </div>
              <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-slate-950 p-4 font-mono text-[11px] leading-5 text-slate-100">{code}</pre>
              <div className="mt-3 flex items-center justify-between gap-4 text-[10px] text-slate-500"><span>完整凭证只在这次生成结果中展示；之后可以单独撤销。</span><button type="button" onClick={() => void copy(code, 'inline-code')} className="shrink-0 rounded px-2 py-1 font-semibold text-teal-700 hover:bg-teal-50">{copied === 'inline-code' ? '已复制' : '复制代码'}</button></div>
            </div>

            {(callPackage.query_params.length > 0 || callPackage.header_params.length > 0 || callPackage.editable_body_keys.length > 0) && (
              <div className="flex flex-wrap items-center gap-2 rounded-xl border border-slate-200 bg-slate-50 px-4 py-3">
                <span className="mr-1 text-[10px] font-semibold text-slate-500">调用方可修改</span>
                {editableLabels(callPackage).map(label => <span key={label} className="rounded-full border border-slate-200 bg-white px-2 py-1 font-mono text-[10px] text-slate-700">{label}</span>)}
              </div>
            )}
          </section>
        )}

        {current.http_enabled && !callPackage && (
          <details className="rounded-xl border border-slate-200 bg-slate-50/70 px-4 py-3 text-xs">
            <summary className="cursor-pointer font-medium text-slate-600">查看平台生成的配置</summary>
            <div className="mt-3 grid grid-cols-1 gap-3 text-[10px] text-slate-500 sm:grid-cols-3">
              <GeneratedField title="地址参数" value={current.proxy_query_keys.join('、') || '无需修改'} />
              <GeneratedField title="业务请求头" value={current.proxy_header_keys.join('、') || '平台固定提供'} />
              <GeneratedField title="请求数据" value={current.proxy_body_keys.map(bodyKeyLabel).join('、') || '平台固定提供'} />
            </div>
          </details>
        )}
      </div>
    </Modal>
  )
}

function AutoStep({ index, title, text }: { index: string; title: string; text: string }) {
  return <div className="rounded-xl border border-slate-200 bg-white p-4"><div className="flex h-6 w-6 items-center justify-center rounded-full bg-violet-100 text-[10px] font-bold text-violet-700">{index}</div><div className="mt-3 text-xs font-semibold text-slate-800">{title}</div><p className="mt-1 text-[10px] leading-4 text-slate-500">{text}</p></div>
}

function GeneratedField({ title, value }: { title: string; value: string }) {
  return <div className="rounded-lg bg-white p-3"><div className="font-semibold text-slate-700">{title}</div><div className="mt-1 break-words">{value}</div></div>
}

function editableLabels(value: ForwardingPackage) {
  return [...new Set([
    ...value.query_params.map(item => item.key),
    ...value.header_params.map(item => item.key),
    ...value.editable_body_keys.map(bodyKeyLabel),
  ])]
}

function bodyKeyLabel(value: string) {
  const parts = value.split('/').filter(Boolean)
    .map(part => part.replaceAll('~1', '/').replaceAll('~0', '~'))
  return parts.join('.') || value
}

function forwardingCode(value: ForwardingPackage, tab: 'curl' | 'python' | 'javascript') {
  const endpoint = `${window.location.origin}${value.path}`
  const queryEntries = value.query_params.map(item => [item.key, item.value || `<${item.key}>`] as const)
  const headerEntries = value.header_params.map(item => [item.key, item.value || `<${item.key}>`] as const)
  if (tab === 'python') return pythonCode(value, endpoint, queryEntries, headerEntries)
  if (tab === 'javascript') return javascriptCode(value, endpoint, queryEntries, headerEntries)
  return curlCode(value, endpoint, queryEntries, headerEntries)
}

function curlCode(value: ForwardingPackage, endpoint: string, queryEntries: readonly (readonly [string, string])[], headerEntries: readonly (readonly [string, string])[]) {
  const query = queryEntries.length
    ? `?${queryEntries.map(([key, item]) => `${encodeURIComponent(key)}=${item}`).join('&')}`
    : ''
  const lines = [`curl -X ${value.method} ${shellQuote(endpoint + query)}`, `  -H ${shellQuote(`${value.key_header}: ${value.secret}`)}`]
  headerEntries.forEach(([key, item]) => lines.push(`  -H ${shellQuote(`${key}: ${item}`)}`))
  if (value.body_template) {
    const contentType = value.body_type === 'json' ? 'application/json' : 'application/x-www-form-urlencoded'
    lines.push(`  -H ${shellQuote(`Content-Type: ${contentType}`)}`)
    lines.push(`  --data-raw ${shellQuote(value.body_template)}`)
  }
  return lines.join(' \\\n')
}

function pythonCode(value: ForwardingPackage, endpoint: string, queryEntries: readonly (readonly [string, string])[], headerEntries: readonly (readonly [string, string])[]) {
  const headers = Object.fromEntries([[value.key_header, value.secret], ...headerEntries])
  if (value.body_template) headers['Content-Type'] = value.body_type === 'json' ? 'application/json' : 'application/x-www-form-urlencoded'
  const parts = [
    'import requests',
    '',
    `url = ${JSON.stringify(endpoint)}`,
    `params = ${JSON.stringify(Object.fromEntries(queryEntries), null, 2)}`,
    `headers = ${JSON.stringify(headers, null, 2)}`,
  ]
  if (value.body_template) parts.push(`body = ${JSON.stringify(value.body_template)}`)
  parts.push('', `response = requests.request(${JSON.stringify(value.method)}, url, params=params, headers=headers${value.body_template ? ', data=body' : ''})`, 'response.raise_for_status()', 'print(response.text)')
  return parts.join('\n')
}

function javascriptCode(value: ForwardingPackage, endpoint: string, queryEntries: readonly (readonly [string, string])[], headerEntries: readonly (readonly [string, string])[]) {
  const headers = Object.fromEntries([[value.key_header, value.secret], ...headerEntries])
  if (value.body_template) headers['Content-Type'] = value.body_type === 'json' ? 'application/json' : 'application/x-www-form-urlencoded'
  const parts = [
    `const url = new URL(${JSON.stringify(endpoint)});`,
    `const params = ${JSON.stringify(Object.fromEntries(queryEntries), null, 2)};`,
    'Object.entries(params).forEach(([key, value]) => url.searchParams.set(key, value));',
    '',
    'const response = await fetch(url, {',
    `  method: ${JSON.stringify(value.method)},`,
    `  headers: ${JSON.stringify(headers, null, 2).replaceAll('\n', '\n  ')},`,
  ]
  if (value.body_template) parts.push(`  body: ${JSON.stringify(value.body_template)},`)
  parts.push('});', 'if (!response.ok) throw new Error(`HTTP ${response.status}`);', 'console.log(await response.text());')
  return parts.join('\n')
}

function shellQuote(value: string) {
  return `'${value.replaceAll("'", "'\\''")}'`
}
