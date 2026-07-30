# 产品文档

本目录回答“平台做什么、为谁做、怎样验收”。

```text
product/
├── README.md
├── overview.md                  产品范围与核心业务链路
├── navigation-business-map.md  用户导航与稳定业务能力
└── requirements/
    ├── README.md                需求规则与记录清单
    ├── template.md              新需求模板
    ├── 0001-current-platform-contract.md
    │                            当前导航、路由与 RBAC 证据基线
    └── 0002-core-data-ontology-runtime-contract.md
                                 数据、发布、Runtime 的可执行现状契约
```

当前功能范围从 `frontend/src/config/navigation.ts`、`frontend/src/App.tsx`、
`backend/app/main.py` 和对应测试交叉核对。页面存在不等于后端能力完整，文档
不得根据 UI 文案推测尚未实现的业务规则。

开始阅读：[产品概览](./overview.md) →
[核心数据流](../architecture/data-flow.md) →
[核心运行契约](./requirements/0002-core-data-ontology-runtime-contract.md) →
[导航与业务能力](./navigation-business-map.md)。
