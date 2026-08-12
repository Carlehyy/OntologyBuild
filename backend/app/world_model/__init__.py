"""世界模型（演化层）— 推演模型的开发、调试与调用记录。

本期边界：
  - 推演模型项目 CRUD + 脚本版本（开发态）
  - 调试执行：复用数据通道的 Jupyter Kernel Gateway 执行通道
    （app.data_channel.pipelines.python_engine），注入 simulate 调用收尾
  - 调用记录：表结构与只读查询 API 先行建好；「发布为 HTTP 推演服务」
    与调用埋点属二期，届时写入方落地，本域查询接口直接可用。

推演模型的脚本契约（与平台统一接口约定一致）：
  def simulate(context, actions, horizon) -> JSON 可序列化结果
    context: dict  — 当前状态快照（数字孪生/图谱事实）
    actions: list  — 候选行动（无干预推演时为空列表）
    horizon: int   — 推演时域（步数或月数，语义由模型自定）
"""
