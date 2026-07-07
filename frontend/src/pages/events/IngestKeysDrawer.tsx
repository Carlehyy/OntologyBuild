import { useState } from 'react'
import { createPortal } from 'react-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  X, KeyRound, Copy, Check, Plus, Ban, ShieldCheck, Terminal, AlertTriangle, Plug,
} from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { LoadingState } from '@/components/ui/LoadingState'
import { eventsApi, type IngestKey } from '@/api/events'

function fmt(iso: string | null): string {
  if (!iso) return '从未'
  const d = new Date(iso)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function CopyBtn({ text, small }: { text: string; small?: boolean }) {
  const [done, setDone] = useState(false)
  return (
    <button
      onClick={() => { navigator.clipboard?.writeText(text); setDone(true); setTimeout(() => setDone(false), 1500) }}
      className={`inline-flex items-center gap-1 ${small ? 'text-xs' : 'text-sm'} text-[var(--color-primary)] hover:opacity-80`}
    >
      {done ? <Check size={13} /> : <Copy size={13} />}{done ? '已复制' : '复制'}
    </button>
  )
}

export default function IngestKeysDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const qc = useQueryClient()
  const [name, setName] = useState('')
  const [scope, setScope] = useState('')
  const [newKey, setNewKey] = useState<IngestKey | null>(null)
  const [err, setErr] = useState('')

  const endpoint = `${window.location.origin}/api/v2/ingest/events`

  const { data: keys, isLoading } = useQuery<IngestKey[]>({
    queryKey: ['ingest-keys'],
    queryFn: eventsApi.listKeys,
    enabled: open,
  })

  const createMut = useMutation({
    mutationFn: () => eventsApi.createKey(name.trim(), scope.trim() || undefined),
    onSuccess: (k) => {
      setNewKey(k); setName(''); setScope(''); setErr('')
      qc.invalidateQueries({ queryKey: ['ingest-keys'] })
    },
    onError: (e: any) => setErr(e?.detail || e?.message || '创建失败（需要管理员权限）'),
  })

  const revokeMut = useMutation({
    mutationFn: (id: string) => eventsApi.revokeKey(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['ingest-keys'] }),
  })

  if (!open) return null

  const curl = `curl -X POST "${endpoint}" \\
  -H "X-API-Key: <你的密钥>" \\
  -H "Content-Type: application/json" \\
  -d '{
    "title": "产线停机",
    "event_type": "设备异常",
    "severity": "high",
    "source_system": "MES",
    "source_ref": "WO-2026-0007",
    "occurred_at": "2026-07-07T09:12:00Z",
    "payload": { "line": "A3", "code": "E502" }
  }'`

  return createPortal(
    <div className="fixed inset-0 z-[80] flex justify-end">
      <div className="absolute inset-0 bg-[var(--color-bg-overlay)]" onClick={onClose} />
      <div className="relative w-full max-w-2xl bg-white shadow-xl border-l border-[var(--color-border)] flex flex-col anim-drawer-in">
        {/* 头部 */}
        <div className="shrink-0 px-6 py-4 border-b border-[var(--color-border)] flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Plug size={18} className="text-[var(--color-primary)]" />
            <h2 className="text-lg font-semibold text-[var(--color-text-primary)]">API 接入 · 第三方上传</h2>
          </div>
          <button onClick={onClose} className="text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)]">
            <X size={20} />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-6 py-5 space-y-6">
          {/* 端点 + curl */}
          <section>
            <h4 className="flex items-center gap-1.5 text-xs font-semibold text-[var(--color-text-tertiary)] uppercase tracking-wide mb-2">
              <Terminal size={13} /> 上传端点
            </h4>
            <div className="flex items-center gap-2 bg-[var(--color-bg-base)] border border-[var(--color-border)] rounded-md px-3 py-2 mb-2">
              <code className="text-xs text-[var(--color-text-primary)] flex-1 break-all">POST {endpoint}</code>
              <CopyBtn text={endpoint} small />
            </div>
            <div className="relative bg-[var(--color-bg-base)] border border-[var(--color-border)] rounded-md p-3">
              <div className="absolute top-2 right-2"><CopyBtn text={curl.replace(/\\\n\s*/g, ' ')} small /></div>
              <pre className="text-xs font-mono text-[var(--color-text-secondary)] overflow-x-auto whitespace-pre">{curl}</pre>
            </div>
            <p className="text-xs text-[var(--color-text-tertiary)] mt-2 leading-relaxed">
              批量上传：请求体传 <code>{'{ "events": [ ... ] }'}</code>。字段 <code>source_system</code> + <code>source_ref</code>
              构成幂等键，重复上传同一 <code>source_ref</code> 不会重复登记。字段名 snake_case / camelCase 均可。
            </p>
          </section>

          {/* 新建密钥 */}
          <section className="border-t border-[var(--color-border)] pt-5">
            <h4 className="flex items-center gap-1.5 text-xs font-semibold text-[var(--color-text-tertiary)] uppercase tracking-wide mb-3">
              <KeyRound size={13} /> 生成密钥（管理员）
            </h4>
            {newKey?.plaintextKey && (
              <div className="mb-3 rounded-md border border-[var(--color-success)] bg-[var(--color-success-bg)] p-3">
                <div className="flex items-center gap-1.5 text-xs font-medium text-[var(--color-success)] mb-1.5">
                  <ShieldCheck size={13} /> 密钥「{newKey.name}」已生成 —— 可在下方列表随时复制
                </div>
                <div className="flex items-center gap-2 bg-[var(--color-bg-elevated)] rounded px-2 py-1.5">
                  <code className="text-xs flex-1 break-all text-[var(--color-text-primary)]">{newKey.plaintextKey}</code>
                  <CopyBtn text={newKey.plaintextKey} small />
                </div>
              </div>
            )}
            {err && (
              <div className="mb-3 flex items-center gap-2 text-xs text-[var(--color-danger)] bg-[var(--color-danger-bg)] rounded-md px-3 py-2">
                <AlertTriangle size={13} /> {err}
              </div>
            )}
            <div className="flex items-end gap-2">
              <Input label="密钥名（= 第三方来源标识）" value={name} onChange={e => setName(e.target.value)}
                placeholder="如：MES产线网关" className="flex-1" />
              <Input label="限定来源系统（可选）" value={scope} onChange={e => setScope(e.target.value)}
                placeholder="如：MES" className="flex-1" />
              <Button onClick={() => createMut.mutate()} loading={createMut.isPending} disabled={!name.trim()}>
                <Plus size={14} /> 生成
              </Button>
            </div>
          </section>

          {/* 密钥列表 */}
          <section className="border-t border-[var(--color-border)] pt-5">
            <h4 className="text-xs font-semibold text-[var(--color-text-tertiary)] uppercase tracking-wide mb-3">已有密钥</h4>
            {isLoading ? (
              <LoadingState />
            ) : keys?.length ? (
              <div className="space-y-2">
                {keys.map(k => {
                  const revoked = !k.enabled || !!k.revokedAt
                  return (
                    <div key={k.id} className={`rounded-lg border border-[var(--color-border)] px-3 py-2.5 ${revoked ? 'opacity-60' : ''}`}>
                      <div className="flex items-center gap-3">
                        <KeyRound size={15} className="text-[var(--color-text-tertiary)] shrink-0" />
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center gap-2">
                            <span className="text-sm font-medium text-[var(--color-text-primary)] truncate">{k.name}</span>
                            {k.allowedSourceSystem && (
                              <span className="px-1.5 py-0.5 rounded text-[10px] bg-[var(--color-bg-hover)] text-[var(--color-text-secondary)]">
                                限 {k.allowedSourceSystem}
                              </span>
                            )}
                            {revoked && <span className="text-[10px] text-[var(--color-danger)]">已吊销</span>}
                          </div>
                          <div className="text-xs text-[var(--color-text-tertiary)]">最近使用 {fmt(k.lastUsedAt)}</div>
                        </div>
                        {!revoked && (
                          <button onClick={() => revokeMut.mutate(k.id)}
                            className="shrink-0 inline-flex items-center gap-1 text-xs text-[var(--color-text-tertiary)] hover:text-[var(--color-danger)]">
                            <Ban size={13} /> 吊销
                          </button>
                        )}
                      </div>
                      {/* 完整密钥：可随时复制 */}
                      {!revoked && (
                        k.plaintextKey ? (
                          <div className="mt-2 flex items-center gap-2 bg-[var(--color-bg-base)] border border-[var(--color-border)] rounded px-2 py-1.5">
                            <code className="text-xs flex-1 break-all text-[var(--color-text-secondary)]">{k.plaintextKey}</code>
                            <CopyBtn text={k.plaintextKey} small />
                          </div>
                        ) : (
                          <div className="mt-2 text-[11px] text-[var(--color-text-tertiary)] font-mono">
                            {k.keyPrefix}…（旧密钥未留存明文，如需复制请重新生成）
                          </div>
                        )
                      )}
                    </div>
                  )
                })}
              </div>
            ) : (
              <p className="text-xs text-[var(--color-text-tertiary)]">还没有密钥，生成一把给第三方使用。</p>
            )}
          </section>
        </div>
      </div>
    </div>,
    document.body,
  )
}
