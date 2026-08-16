# 评估器设计与数据飞轮 SOP（本体助手）

接入观测之后，AgentLoop 的核心价值在评估与数据集闭环。本文件给出针对**本体助手**
（Super Assistant）的可执行方案：怎么建评估器、怎么挖 Bad Case、怎么把结论变成优化。

## 0. 目标

找出本体助手可以优化的点，并形成数据飞轮：

```
线上 Trace 观测 → 抽样评估（Agent-as-a-Judge）→ 挖掘 Bad Case（Trace2Dataset）
→ 定向优化（prompt / 技能 / 工具 / 压缩阈值 / 模型）→ Playground 对比 → 上线
→ 持续抽样评估拦截退化 → （循环）
```

与平台已有能力的分工：`reflection_service`（`backend/app/super_assistant/`）是助手
**会话内**的微复盘内循环；AgentLoop 是**跨会话、跨版本**的外循环评估，两者互补。

## 1. 观测阶段看什么

接入后控制台「观测」即可按 Trace 看：

- **成本**：单会话 Token 消耗结构（本体助手上下文压缩阈值 64k、微复盘调用、
  PLAN/EXECUTE/VERIFY 多轮消耗）——找 Token 膨胀热点；
- **质量**：工具调用失败与重试、`[GOAL_FAILED]` 的会话占比——找效果差的场景；
- **性能**：端到端延迟构成（模型 / MCP 工具 / 记忆检索占比）——找慢工具；
- **安全**：MCP 工具调用留痕审计。

平台数据库里 `super_assistant_messages.token_usage`、`super_assistant_messages.steps`、
`super_assistant_tool_runs` 已有结构化记录，可与 AgentLoop Trace 互相印证。

## 2. 评估器（Agent-as-a-Judge）

评估器是带 Prompt / Skill / MCP 能力的元 Agent，对单次 Trace/Trajectory 打分。

### 2.1 预置评估器（先直接用）

- 任务完成度：本体助手自主模式已在最终答复输出 `[GOAL_COMPLETE]` /
  `[GOAL_FAILED]` 标记，天然是任务完成度标签；
- 幻觉：检查回复中引用的本体实体（类/属性/实例）是否真实存在于用户本体库；
- 正确性：本体构建类任务的输出是否符合目标本体 schema。

### 2.2 自定义评估器「本体构建正确性」（建议第二步建）

评估 Prompt 要点（挂 MCP 工具后评估器可回放轨迹并调用平台 API 核验）：

1. 输入：完整轨迹（用户目标 → 每一步工具调用与返回值 → 最终答复）。
2. 核验维度：
   - 生成的本体结构（类、属性、关系、实例）是否符合 schema 约束；
   - 工具调用序列是否合理（是否有无谓重试、错误工具选择）；
   - 最终答复与用户目标的覆盖度、是否编造不存在的实体。
3. 输出：0-100 分 + 结构化理由（问题定位到「Prompt / 工具 / 模型」三类根因之一）。

评估器结果用于发现 Bad Case、监控线上质量、验证版本变更是否退化。

### 2.3 评估任务与采样

- 数据源：线上链路（Trace）或数据集；先按 **5%-10% 抽样** 控制成本（一次评估约
  10 AI 积分 ≈ 0.1 元）。
- 高频场景先跑：本体构建、关系补全、问答三类任务各建一个评估任务。
- 当前产品限制：评估器按单次 Trace 评估；多轮 Session 评估预计 2026-09 上线。

## 3. 挖 Bad Case：Trace2Dataset

在「数据中心」配置 Pipeline：数据源接入（线上 Trace）→ 数据降维（过滤/去重/采样/
聚类）→ 特征提取 → 写入数据集。自动沉淀：

- **BadCase 数据集**：低分 Trace（按评估器结果过滤）——待优化清单；
- **Golden 数据集**：高分 Trace——回归基准。

## 4. 周度优化闭环（SOP）

1. **看趋势**：评估分析页看平均分/分布/耗时趋势，标注波动点（模型变更、prompt 变更）。
2. **钻样本**：从低分 BadCase 下钻轨迹，定位根因（Prompt 歧义 / 技能缺失 /
   工具失效 / 模型幻觉）。
3. **定优化**：改 system prompt（`backend/app/super_assistant/runtime.py` 的
   `_system_prompt`）、技能包（`skill_store.py`）、上下文压缩阈值
   （`_DEFAULT_CONTEXT_TOKENS`）、或换模型配置。
4. **对比验证**：AgentLoop Playground 实验（Agent 类型实验，预计 2026-06-30 前
   上线）用 Golden/BadCase 数据集对比新旧版本，达标再上线。
5. **上线回归**：部署后继续抽样评估，分数劣化即回滚。

## 5. 安全与成本提醒

- 对话内容与工具参数会随 Trace 上报阿里云；涉及客户数据时在控制台开启端侧脱敏
  （手机号/身份证/邮箱/IP/银行卡）。
- 计费参考：AI 积分 0.01 元/积分、执行 0.001 元/次、数据集存储 0.00004 元/条/天；
  新用户首月有免费额度（10,000 积分 / 2,000 次执行）。

参考：[评估概述](https://help.aliyun.com/zh/document_detail/3042179.html) ·
[什么是 AgentLoop](https://help.aliyun.com/zh/document_detail/3033860.html)
