# 架构决策记录

ADR 记录影响多个模块、长期维护或难以回退的技术决策。小型实现细节写入迭代
记录即可；API 兼容、数据库策略、目录边界、部署模型和安全信任边界通常需要
ADR。

命名格式：

```text
NNNN-short-title.md
```

编号递增且不复用。决策被替代时保留原文，将状态改为 `Superseded by
ADR-NNNN`。复制 [template.md](./template.md) 创建新记录。

## 当前记录

- [ADR-0001：按稳定业务能力组织源码](./0001-business-domain-structure.md)
- [ADR-0002：平台概览采用前端 feature 边界](./0002-frontend-overview-feature-boundary.md)
- [ADR-0003：退役文档到本体的遗留抽取链路](./0003-retire-legacy-document-ontology-extraction.md)
