# 哨兵引擎(Sentinel Engine)核心逻辑说明书

> 反应式本体运行时 —— 让 OntologyBuild 这个平台基座上的本体"活起来"。
> 监听对象实体状态变化 → 跨对象条件判断 → 命中后执行动作。

本文件描述哨兵引擎的**完整功能需求与实现逻辑**,供开发、配置、验证三方对齐。文末附**未完成任务事项**。

---

## 1. 定位与目标

OntologyBuild 是一个**平台基座**:用户在图谱编辑器中编排本体结构(抽象实体 / 对象实体 / 实体关系 / 执行动作 / 激活函数,参考 Palantir Foundry)。但本体本身是**静态的类型定义**,不会自己运转。

**哨兵引擎解决的根本问题:让本体跑起来。** 即回答三件事:
1. **什么变了** —— 监听对象实体的属性状态变化;
2. **是否该触发** —— 在数据变化或定期任务时,判断条件是否满足;
3. **做什么** —— 命中后自动执行用户绑定的动作。

哨兵引擎是**通用机制(mechanism)**,不绑定任何具体业务。具体"触发后干什么"(发通知、起工单、改状态、调外部系统、风险冒泡等)由**动作(Action)层**实现。ITGC 风险治理只是跑在这个基座上的**一个场景**,而非引擎内核的一部分。

设计严格参考 Palantir Foundry 的 **Automate / Object Monitors** 范式与 ITGC 平台的 **Skill / 规则引擎** 实践。

---

## 2. 核心概念

### 2.1 哨兵(Sentinel)
独立于动作的**一等公民**。一个哨兵 = **监听范围(可跨多个对象)+ 触发条件 + 触发时机 → 一组动作**。

哨兵挂在本体(ontology)下,一个本体可建任意多个哨兵,各自独立评估、互不影响。

### 2.2 监听范围:跨对象绑定(bindings)
哨兵可同时监听多个对象类型,每个用一个**别名**引用:

```jsonc
bindings: [
  { "alias": "a", "objectTypeId": "<订单>",  "filter": null },
  { "alias": "b", "objectTypeId": "<商家>",  "filter": null }
]
```

条件、动作目标都通过别名引用对应对象。

### 2.3 对象关联(links)
当条件跨多个对象(如 `订单.金额 > 商家.信用额度`)时,需要知道"哪个订单配哪个商家"。配对依据来自**对象之间的关系**:

```jsonc
links: [ { "from": "a", "linkTypeId": "<归属>", "to": "b" } ]
```

- 关系**底层用链接实例(LinkInstance)**表达,是一等公民的边(对标 Foundry 的 foreign-key / join-table backed Link)。
- 前端在保存哨兵时,**依据本体已有的实体关系自动推断 links**(两对象类型间存在唯一关系即自动采用),用户无需手选;多条候选时提示。
- 若两对象间**无关系**,退化为全组合(笛卡尔积)——通常语义不正确,见 §7 未完成项。

### 2.4 触发条件(condition)
最终在"命中元组"上求值的布尔表达式,经由前端的**结构化条件编辑器**生成:

- 逐行 `[左值] [运算符] [右值]`,左值为 `别名.属性`,右值可为**常量值**或**另一对象的属性**;
- 行间逻辑 **AND / OR**(`condition_logic`);
- 运算符:`== != > >= < <= contains`,按属性类型自动给可选项;
- 前端把结构化行(`condition_rows`,用于回显)**编译成单个表达式字符串**(`condition`,用于求值);
- 提供"高级模式"直接书写表达式;
- 求值由后端的 `safe_eval` 沙箱执行(支持 `对象.属性` 取值,缺失键返回 None)。

### 2.5 执行动作(action_ids)
命中后**依次执行**的一组动作(ActionType id 列表)。动作复用既有的 `execute_action` 引擎,动作内部可自定义副作用(`create_object` / `update_property` / `create_link` / `notification` / `webhook`)。其中 `notification` 已做实:写入可查询的内部通知收件箱,并支持**沿链接跳转解析收件人**(如订单→归属→商家邮箱)。

动作作用的目标对象 = `primary_alias` 对应的命中实例(默认首个绑定)。

---

## 3. 触发时机:三个入口

三种入口最终都汇入**同一个评估器**,只是"评估谁"的范围不同:

| 入口 | 触发时机 | 评估范围 | 实现 |
|------|----------|----------|------|
| **手动触发** | 用户点击 | 本体下全部启用的哨兵 | `POST .../sentinels/run` → `run_manual` |
| **变化驱动** | 对象实例属性变化(提交后) | 绑定引用了该对象类型、且开启 `on_change` 的哨兵 | CDC `after_commit` → 后台线程 → `run_for_change` |
| **定期扫描** | 到达各哨兵 `scan_interval_seconds` | 开启 `on_schedule` 且到期的哨兵(跨本体) | 后台 worker 每 `SENTINEL_SCAN_TICK` 秒 tick → `run_scheduled` |

**变化驱动(CDC)机制**:在 SQLAlchemy 的 `before_flush`/`after_commit` 上挂监听,统一捕获对象实例(ObjectInstance)的新增/属性变更——无论变化来自数据管道投影、动作回写、手动编辑还是采集器,都从这一个口子出。提交后用独立 Session 在后台线程触发评估,不阻塞主请求。

> 设计要点:**监听对象的"状态",而非监听数据源**。这是 Foundry 的核心抽象——监听层不关心属性怎么变的,只盯对象的属性状态变了。

---

## 4. 触发语义:边沿触发(核心)

参考 Foundry Automate 的"对象进入集合才触发"语义,**避免条件持续满足时重复触发(刷屏)**。

### 4.1 评估流程(差分式)
每次评估(无论哪个入口):

1. **算当前命中集**:解析跨对象绑定(别名 + 链接遍历 + 必要时笛卡尔)得到候选元组,对每个元组求值 `condition`,得到当前命中的元组集合。
2. **取上次命中集**:从 `sentinel_match_state` 表读出该哨兵上次记录的命中键。
3. **做差**:
   - `进入(entered) = 当前 − 上次`
   - `离开(left) = 上次 − 当前`
4. **按触发方式执行动作**(见 4.2)。
5. **更新命中状态**:删除离开的、upsert 当前的。
6. **记录触发日志**(SentinelFiring)。

**命中键(match_key)**:单对象时为命中实例 id;跨对象时为整个匹配元组的稳定签名(`a=oid|b=mid`)。同键去重。

### 4.2 触发方式(trigger_mode)
| 取值 | 含义 | 适用 |
|------|------|------|
| `on_enter`(默认) | 仅对"新进入"的命中执行动作 | 一次性动作(发通知、起请求) |
| `on_enter_leave` | 进入触发 + 离开时也触发(动作可据 `edge` 字段做收尾) | 需要"条件消除后收尾"的场景 |
| `run_on_all` | 每轮对全部当前命中执行(电平/批量) | 少数确实要周期性全量处理的场景 |

**效果**:`on_enter` 模式下,一个持续满足的命中同时在"上次"和"当前"集中 → 不在"进入"里 → 不重复触发。条件消除再恢复 → 重新进入 → 再次触发。

### 4.3 至少一次 + 幂等
与 Foundry 一致,效果是**至少一次**语义(极少数情况可能重复执行)。因此:
- 用边沿触发去重作为主保险;
- 动作应实现为**幂等操作**(如以确定 id 创建资源,重复执行只生效一次);
- 提供 **`muted`(静默)** 开关:仍评估并记录命中,但不执行动作——可用于**上线前观察**(影子模式)。

### 4.4 断环
执行哨兵动作期间置上下文标记 `in_sentinel_run`,CDC 据此**抑制由动作回写引发的即时再触发**,避免"动作改对象 → 再触发 → 再改"死循环;被抑制的后续条件由定期扫描兜底。

---

## 5. 数据模型

### 5.1 `sentinels`(哨兵配置)
| 字段 | 类型 | 说明 |
|------|------|------|
| id, ontology_id | str | 主键 / 所属本体 |
| name, display_name, description | str | 名称 |
| bindings | JSON | 监听绑定 `[{alias, objectTypeId, filter}]` |
| links | JSON | 关系约束 `[{from, linkTypeId, to}]`(前端自动推断) |
| condition | text | 求值表达式(前端编译) |
| condition_rows | JSON | 结构化条件行(回显用) |
| condition_logic | str | 行间逻辑 `and`/`or` |
| primary_alias | str | 动作目标别名 |
| action_ids | JSON | 命中后依次执行的动作 id 列表 |
| on_change | bool | 是否变化驱动 |
| on_schedule | bool | 是否定期扫描 |
| scan_interval_seconds | int | 扫描间隔(默认 300) |
| last_scanned_at | datetime | 上次扫描时间 |
| trigger_mode | str | `on_enter` / `on_enter_leave` / `run_on_all` |
| muted | bool | 静默(评估但不执行动作) |
| enabled | bool | 启停 |
| status | str | `draft` / `published` |

### 5.2 `sentinel_match_state`(边沿触发的"上次匹配集",运行时状态)
| 字段 | 说明 |
|------|------|
| sentinel_id | 所属哨兵 |
| match_key | 命中键(primary 实例 id 或元组签名) |
| match_detail | 命中元组明细 `{alias: instanceId}` |
| first_seen_at / last_seen_at | 首次/最近命中时间 |

> 与哨兵配置分离,作为高频读写的运行时状态独立存放(并发更安全、便于查"当前命中了哪些")。

### 5.3 `sentinel_firings`(触发日志)
| 字段 | 说明 |
|------|------|
| sentinel_id, sentinel_name | 哨兵 |
| trigger_source | `manual` / `change` / `schedule` |
| matches, match_count | 本次当前命中元组及数量 |
| entered, left | 本次新进入 / 离开的命中键 |
| action_results | 各动作执行结果 `[{actionId, targetInstanceId, edge, status, logId, effects}]` |
| status | `fired` / `no_change` / `no_match` / `muted` / `error` |
| duration_ms | 耗时 |

### 5.4 `sentinel_notifications`(通知收件箱)
`notification` 动作副作用的真实落地点(channel / recipient / subject / body / related_object_id / action_id / status)。

---

## 6. 代码结构与接口

### 6.1 后端
```
backend/app/ontologies/sentinels/models.py     # Sentinel / SentinelMatchState / SentinelFiring / Notification
backend/app/ontologies/sentinels/
  ├── evaluator.py    # 核心:跨对象绑定解析 + 链接遍历 + 条件求值 + 边沿差分 + 执行动作
  ├── engine.py       # 三入口:run_manual / run_for_change / run_scheduled
  ├── cdc.py          # 变化捕获(SQLAlchemy before_flush/after_commit)+ 断环 + 后台派发
  ├── scan_worker.py  # 定期扫描后台线程
  └── router.py       # REST API
backend/scripts/demos/sentinel_demo.py       # 端到端:跨对象+多动作+三入口
backend/scripts/demos/sentinel_edge_demo.py  # 端到端:边沿触发(不重复触发)
```

启动接线(`main.py` lifespan):注册模型建表 → `register_cdc()` → `start_scan_worker()`。

### 6.2 REST API
挂载前缀 `/api/v1/ontologies/{ontology_id}/sentinels`:

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | 哨兵列表 |
| POST | `/` | 创建 |
| GET | `/{id}` | 详情 |
| PUT | `/{id}` | 更新 |
| DELETE | `/{id}` | 删除 |
| POST | `/{id}/toggle` | 启停 |
| POST | `/run` | 手动触发(全量评估) |
| GET | `/firings` | 触发日志 |
| GET | `/notifications` | 通知收件箱 |

请求/响应统一 camelCase(schema 继承 `CamelModel`,驼峰/蛇形均接受)。

### 6.3 前端
```
frontend/src/api/sentinelApi.ts                                  # API 封装
frontend/src/palantir-graph/components/panels/SentinelPanel.tsx  # 图谱编辑器「哨兵引擎」面板
```
入口:图谱编辑器悬浮菜单 →「哨兵引擎」。面板提供:哨兵列表/启停/删除、句子式条件编辑(对象用中文名、运算符中文、AND/OR、单行展示)、关系自动推断展示、多动作多选、触发时机(变化/扫描+间隔)、触发方式选择、静默开关、手动触发、触发日志查看。

### 6.4 环境开关
| 变量 | 默认 | 说明 |
|------|------|------|
| `SENTINEL_AUTO_DISPATCH` | 1 | 关闭则变化驱动不自动派发(改由定时/手动) |
| `SENTINEL_SCAN_ENABLED` | 1 | 关闭则不启动定期扫描 worker |
| `SENTINEL_SCAN_TICK` | 15 | 扫描 tick 秒数 |
| `AUTOMATION_*` | — | (历史保留,见代码) |

---

## 7. 上游依赖现状

哨兵评估读取的上游数据:对象实例(`fo_object_instances.properties`,扁平字典)、链接实例(`fo_link_instances`)、对象类型属性 schema、动作。核对结论:

- ✅ **对象实例**:存储、创建路径(UI/整体保存/API/数据管道投影/采集器/动作回写)齐备,且全走 ORM,CDC 可捕获。
- ✅ **属性 schema**:实例 `properties` 键 = 对象类型属性 `name`,与条件编辑器一致。
- ✅ **动作 + execute_action**:可用,notification 已做实(含链接解析收件人)。
- ⚠️ **链接实例**:后端 API / 数据管道投影 / 采集器 / 动作 create_link / 整体保存均可创建;但**前端缺少"为两个实例手动建立关系"的 UI**。真实场景下链接实例主要由**数据管道(引用列→边)**生成。
- ⚠️ **CDC 只监听对象实例属性变化,不监听链接实例增减**;仅改关系不改属性时,变化驱动不触发,由定期扫描兜底。

**数据进入方式**:按"源"采集,源数据携带指向其他对象的**引用列**,采集器/投影把节点 + 关联节点 + 边一起实例化(关系从引用列派生),多源经 `external_id` 去重合并为一张图;状态遥测按 id upsert 逐节点更新属性。

---

## 8. 验证

两个端到端自检脚本(均基于内存/独立 sqlite,可直接运行):

- `cd backend && python -m scripts.demos.sentinel_demo` —— 验证**跨对象条件**(订单金额 > 商家信用额度,经"归属"关联)+ **多动作**(通知 + 标记高风险)+ **三入口**(手动/变化/扫描);未超额订单正确忽略。
- `cd backend && python -m scripts.demos.sentinel_edge_demo` —— 验证**边沿触发**:进入即触发、持续为真不重复触发、新进入单独触发、离开移除命中、重新进入再触发。

二者均通过。

---

## 9. 未完成任务事项(TODO)

按落地优先级排列。当前已完成"第一档"中的边沿触发,其余待办:

### 第一档 · 真实运行的硬门槛
- [x] **边沿触发 / 去重**(已完成):进入集合才触发,持续满足不重复。
- [ ] **影子试跑 / 安全预览**:已有 `muted`(静默评估)与动作 `dry_run` 基础,但缺独立的"干跑预览"端点——评估并返回"会命中哪些、会触发什么",不产生任何副作用,供上线前验证。
- [ ] **published 生效**:目前三入口按 `enabled` 评估,未按 `status` 区分;应做到**仅 `published` 的哨兵真触发,`draft` 仅在影子/预览中运行**。
- [ ] **跑通真实数据**:针对真实本体,把"引用列 → 链接实例"的采集映射端到端验证一遍(目前仅在 demo 数据中验证)。

### 第二档 · 跨对象正确性
- [ ] **评估语义分流**:把条件区分为"节点自身条件"(按"存在满足的实例"独立判断)与"跨节点条件"(才需要配对);避免跨节点不可达时**默默走笛卡尔积**导致误配对。这也支撑 Skill 式的多节点独立条件。
- [ ] **关系解析去顺序依赖 + 多路径显式**:当前 `_resolve_tuples` 按 bindings 顺序扩展、且**只取第一条匹配的边**,导致结果对填写顺序敏感、多路径(A→B→C 与 A→D→C)时不可控。应改为基于关系图的解析,多路径时**要求用户显式指定走哪条关系**,不替用户猜。
- [ ] **连通性校验**:以"配对比较"为语义时提示"所选对象需整体连通(可达即可,不必两两直连)";不连通时给出提示而非静默全组合。

### 第三档 · 通往可解释 / 多场景
- [ ] **多跳遍历 + 证据链**:把单跳遍历升级为任意多跳路径遍历,并**记录走过的节点和边作为证据链**(可解释性 = "为什么触发",所有场景通用,非仅风险引擎)。这是哨兵进化为风险引擎内核的关键一步。
- [ ] **动作类型可扩展**:当前动作副作用类型固定(create_object/update_property/create_link/notification/webhook)。作为平台基座,应支持**场景注册自定义动作 / 副作用**(起工单、调审批流、写外部系统、风险冒泡等),而非把各场景逻辑硬编码进引擎。
- [ ] **webhook 做实**:目前仅记录、不实际外呼,需补真实 HTTP 调用。
- [ ] **链接遍历多跳收件人解析**:notification 的链接解析目前为单跳,需支持多跳(订单→商品→供应商)。

### 运维 / 健壮性
- [ ] **跨线程/级联的环路保护**:当前 `in_sentinel_run` 抑制为同线程内有效;跨异步边界的级联依赖定期扫描兜底,后续可引入显式的因果深度上限。
- [ ] **并发与规模**:`MAX_TUPLES=1000` 为防笛卡尔爆炸的兜底;大规模数据下需要分批/索引优化(对标 Foundry 的 batch size / 分区)。
- [ ] **可观测性**:补充"未触发原因"、评估指标、失败重试/死信策略。
- [ ] **前端建关系 UI(可选)**:为小规模/测试场景提供"在两个对象实例间建立链接"的界面(生产主路径仍为数据管道)。

---

*本说明书随代码同步维护;如与代码不符,以代码为准并及时更新本文件。*
