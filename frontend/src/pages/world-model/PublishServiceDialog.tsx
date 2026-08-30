import { useEffect, useState } from 'react'
import { Loader2, Plus, Rocket, X } from 'lucide-react'

import { ontologyApi } from '@/api/ontologies'
import {
  apiError,
  worldModelApi,
  type ScriptVersionItem,
  type ServicePrecondition,
  type WorldModelProjectDetail,
  type WorldModelServiceInfo,
} from '@/api/worldModel'
import { Modal } from '@/components/ui/Modal'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { useToast } from '@/components/ui/Toast'

interface OntologyOption {
  id: string
  name: string
}

interface ObjectTypeOption {
  id: string
  label: string
}

/**
 * 发布为推演服务：选定冻结版本 + 本体语义注册（适用对象类型 / 前置条件）。
 * 一个项目对应一个在线服务，重复发布即覆盖更新。
 */
export default function PublishServiceDialog({ open, onClose, project, versions, service, onPublished }: {
  open: boolean
  onClose: () => void
  project: WorldModelProjectDetail
  versions: ScriptVersionItem[]
  service: WorldModelServiceInfo | null
  onPublished: (service: WorldModelServiceInfo) => void
}) {
  const { toast } = useToast()
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [versionId, setVersionId] = useState('')
  const [ontologyId, setOntologyId] = useState('')
  const [objectTypeIds, setObjectTypeIds] = useState<string[]>([])
  const [preconditions, setPreconditions] = useState<ServicePrecondition[]>([])
  const [ontologies, setOntologies] = useState<OntologyOption[]>([])
  const [objectTypes, setObjectTypes] = useState<ObjectTypeOption[]>([])
  const [loadingOntologies, setLoadingOntologies] = useState(false)
  const [loadingObjectTypes, setLoadingObjectTypes] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')

  // 打开时初始化表单（重新发布时回填当前服务的注册信息）
  useEffect(() => {
    if (!open) return
    setError('')
    setName(service?.name || project.name)
    setDescription(service?.description || project.description || '')
    setVersionId('')
    setOntologyId(service?.applicable_object_types?.ontology_id || '')
    setObjectTypeIds(service?.applicable_object_types?.object_type_ids || [])
    setPreconditions(service?.preconditions || [])
    setLoadingOntologies(true)
    ontologyApi.list({ page_size: 200 })
      .then(result => setOntologies(
        result.items
          .filter(item => item.current_release_id)
          .map(item => ({ id: item.id, name: item.name })),
      ))
      .catch(err => setError(apiError(err)))
      .finally(() => setLoadingOntologies(false))
  }, [open, project, service])

  // 本体变化时加载其对象类型（实体）
  useEffect(() => {
    if (!ontologyId) { setObjectTypes([]); return }
    setLoadingObjectTypes(true)
    ontologyApi.listEntities(ontologyId)
      .then(entities => setObjectTypes(entities.map(entity => ({
        id: entity.id,
        label: entity.name_cn || entity.name_en || entity.id,
      }))))
      .catch(err => setError(apiError(err)))
      .finally(() => setLoadingObjectTypes(false))
  }, [ontologyId])

  const toggleObjectType = (id: string) => {
    setObjectTypeIds(current => current.includes(id)
      ? current.filter(item => item !== id)
      : [...current, id])
  }

  const canSubmit = Boolean(
    name.trim() && ontologyId && objectTypeIds.length > 0 && !submitting,
  )

  const submit = async () => {
    if (!canSubmit) return
    setSubmitting(true)
    setError('')
    try {
      const published = await worldModelApi.publishService(project.id, {
        version_id: versionId || null,
        name: name.trim(),
        description: description.trim(),
        applicable_ontology_id: ontologyId,
        applicable_object_type_ids: objectTypeIds,
        preconditions: preconditions.filter(item => item.object_type_id),
      })
      toast({ tone: 'success', title: service ? '已重新发布并上线' : '发布成功，服务已上线' })
      onPublished(published)
      onClose()
    } catch (err) {
      setError(apiError(err))
    } finally {
      setSubmitting(false)
    }
  }

  const selectClass = 'h-9 w-full rounded-md border border-[var(--color-border)] bg-card px-3 text-sm text-[var(--color-text-primary)] focus:border-ring focus:outline-none focus:ring-1 focus:ring-ring'
  const labelClass = 'mb-1.5 block text-sm font-medium text-[var(--color-text-primary)]'

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={service ? '重新发布推演服务' : '发布为推演服务'}
      description="发布即上线：选定冻结版本并完成本体语义注册后，服务会获得对外调用端点，重复发布将覆盖更新。"
      headerIcon={<Rocket size={17} />}
      size="lg"
      footer={(
        <>
          <Button variant="ghost" onClick={onClose} disabled={submitting}>取消</Button>
          <Button
            onClick={() => void submit()}
            loading={submitting}
            disabled={!canSubmit}
            className="bg-brand text-white hover:bg-brand-deep active:bg-brand-deep disabled:bg-muted disabled:text-muted-foreground disabled:shadow-none"
          >
            {service ? '重新发布' : '发布并上线'}
          </Button>
        </>
      )}
    >
      <div className="space-y-5">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Input
            label="服务名称"
            required
            maxLength={200}
            value={name}
            onChange={event => setName(event.target.value)}
            placeholder="例如：台区负荷短期推演服务"
            className=" focus:border-ring focus:ring-ring"
          />
          <div>
            <label className={labelClass}>发布版本</label>
            <select value={versionId} onChange={event => setVersionId(event.target.value)} className={selectClass} aria-label="选择发布版本">
              <option value="">最新保存版本（v{versions[0]?.version_no ?? '-'}）</option>
              {versions.map(version => (
                <option key={version.id} value={version.id}>v{version.version_no}</option>
              ))}
            </select>
          </div>
        </div>

        <div>
          <label className={labelClass}>服务描述</label>
          <textarea
            value={description}
            onChange={event => setDescription(event.target.value)}
            maxLength={500}
            rows={2}
            placeholder="说明该服务回答什么推演问题、适用边界"
            className="w-full resize-none rounded-md border border-[var(--color-border)] bg-card px-3 py-2 text-sm text-[var(--color-text-primary)] placeholder:text-[var(--color-text-tertiary)] focus:border-ring focus:outline-none focus:ring-1 focus:ring-ring"
          />
        </div>

        <div className="rounded-xl border border-[var(--color-border)] bg-muted p-4">
          <p className="text-sm font-medium text-[var(--color-text-primary)]">本体语义注册<span className="ml-0.5 text-[var(--color-danger)]">*</span></p>
          <p className="mt-0.5 text-[11px] leading-4 text-[var(--color-text-tertiary)]">
            声明该服务适用的业务对象类型，值必须引用本体概念——Agent 据此做结构化工具检索，而不是靠文字描述猜。
          </p>
          <div className="mt-3 grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div>
              <label className={labelClass}>所属本体</label>
              <select
                value={ontologyId}
                onChange={event => { setOntologyId(event.target.value); setObjectTypeIds([]); setPreconditions([]) }}
                className={selectClass}
                aria-label="选择所属本体"
                disabled={loadingOntologies}
              >
                <option value="">{loadingOntologies ? '加载本体列表…' : '请选择本体'}</option>
                {ontologies.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
              </select>
            </div>
            <div>
              <label className={labelClass}>适用对象类型（可多选）</label>
              <div className="max-h-36 overflow-y-auto rounded-md border border-[var(--color-border)] bg-card px-2 py-1.5" aria-label="适用对象类型">
                {!ontologyId ? (
                  <p className="px-1 py-2 text-xs text-[var(--color-text-tertiary)]">请先选择本体</p>
                ) : loadingObjectTypes ? (
                  <p className="flex items-center gap-1.5 px-1 py-2 text-xs text-[var(--color-text-tertiary)]"><Loader2 size={12} className="animate-spin" /> 加载对象类型…</p>
                ) : objectTypes.length === 0 ? (
                  <p className="px-1 py-2 text-xs text-[var(--color-text-tertiary)]">该本体暂无对象类型</p>
                ) : (
                  objectTypes.map(item => (
                    <label key={item.id} className="flex cursor-pointer items-center gap-2 rounded px-1 py-1 text-sm text-[var(--color-text-primary)] hover:bg-muted">
                      <input
                        type="checkbox"
                        checked={objectTypeIds.includes(item.id)}
                        onChange={() => toggleObjectType(item.id)}
                        className="h-3.5 w-3.5 rounded border-border accent-[var(--color-nav-bg)]"
                      />
                      <span className="truncate">{item.label}</span>
                    </label>
                  ))
                )}
              </div>
            </div>
          </div>

          <div className="mt-3">
            <div className="flex items-center justify-between">
              <label className={labelClass}>前置条件（可选）</label>
              <button
                type="button"
                onClick={() => setPreconditions(current => [...current, { object_type_id: objectTypes[0]?.id || '', min_count: 1 }])}
                disabled={!ontologyId || objectTypes.length === 0}
                className="inline-flex h-7 items-center gap-1 rounded-md border border-[var(--color-border)] px-2 text-[11px] text-[var(--color-text-secondary)] transition-colors hover:bg-muted disabled:opacity-40"
              >
                <Plus size={12} /> 添加条件
              </button>
            </div>
            {preconditions.length > 0 && (
              <div className="space-y-2">
                {preconditions.map((item, index) => (
                  <div key={index} className="flex items-center gap-2">
                    <select
                      value={item.object_type_id}
                      onChange={event => setPreconditions(current => current.map((row, i) => i === index ? { ...row, object_type_id: event.target.value } : row))}
                      className={`${selectClass} flex-1`}
                      aria-label={`前置条件 ${index + 1} 对象类型`}
                    >
                      {objectTypes.map(type => <option key={type.id} value={type.id}>{type.label}</option>)}
                    </select>
                    <span className="shrink-0 text-xs text-[var(--color-text-tertiary)]">数量 ≥</span>
                    <input
                      type="number"
                      min={1}
                      value={item.min_count}
                      onChange={event => setPreconditions(current => current.map((row, i) => i === index ? { ...row, min_count: Math.max(1, Number(event.target.value) || 1) } : row))}
                      className="h-9 w-20 rounded-md border border-[var(--color-border)] bg-card px-2 text-sm focus:border-ring focus:outline-none"
                      aria-label={`前置条件 ${index + 1} 最小数量`}
                    />
                    <button
                      type="button"
                      onClick={() => setPreconditions(current => current.filter((_, i) => i !== index))}
                      aria-label={`删除前置条件 ${index + 1}`}
                      className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-muted-foreground"
                    >
                      <X size={13} />
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        {error && <p role="alert" className="text-sm text-[var(--color-danger)]">{error}</p>}
      </div>
    </Modal>
  )
}
