# 需求文档

本目录保存仍需被实现、维护或验收的产品需求。需求文档与迭代记录不能混用：

- 需求描述期望行为和验收条件；
- ADR 描述关键技术决策及取舍；
- 迭代记录描述实际修改、测试、上线和回滚证据；
- Changelog 描述用户可感知的发布结果。

每份需求至少包含：状态、负责人、背景、范围、非目标、用户流程、权限、
数据/API 影响、验收条件和关联迭代。

## 当前索引

| 文档 | 状态 | 范围 |
|---|---|---|
| [0001 当前平台导航与访问控制契约](./0001-current-platform-contract.md) | Accepted（现状证据基线） | 从源码与自动化测试固化当前导航、Hash 路由、menu key 与 RBAC 行为 |
| [0002 核心数据、本体发布与运行时契约](./0002-core-data-ontology-runtime-contract.md) | Accepted（现状证据基线） | 固化数据生产、Draft/Trial/Promote、发布后 Mapping/Sentinel/Action 的状态门、权限、幂等与回滚 |
| [0003 从本体卡片直达本体助手](./0003-ontology-agent-deep-link.md) | Accepted | 从本体管理卡片通过 Hash 深链进入助手并自动选择当前发布版本 |

复制 [template.md](./template.md) 创建需求。需求被替代时保留原文，将状态改为
`Superseded` 并链接替代文档，不要直接删除决策上下文。
