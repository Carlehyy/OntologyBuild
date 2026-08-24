/* 本体草稿审阅抽屉的纯逻辑：应用成功后的图谱跳转目标。
   与 DraftReviewDrawer.tsx 解耦（无 React 依赖），便于 node:test 单测。 */

/**
 * apply 成功后的图谱链接：后端返回 versionId 时落到该版本的草稿视图
 * （合并路径=目标草稿版本，新建路径=v0 基线）；旧后端缺 versionId 时回退到运行版图谱。
 */
export function appliedGraphPath(result: { ontologyId: string; versionId?: string | null }): string {
  const base = `/ontologies/${result.ontologyId}/graph`
  return result.versionId
    ? `${base}?versionId=${encodeURIComponent(result.versionId)}`
    : base
}
