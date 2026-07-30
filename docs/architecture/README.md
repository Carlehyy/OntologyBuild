# 架构文档

本目录回答“系统现在怎样组成、为什么这样组织、哪些契约不能破坏”。

```text
architecture/
├── README.md
├── overview.md           进程、存储与代码边界
├── module-map.md         导航到源码/API/测试的统一定位
├── data-flow.md          核心业务数据流
├── backend-modules.md    后端 canonical 与兼容层台账
├── frontend-routing.md   路由、导航和权限事实源
└── adr/                  长期架构决策
```

架构描述以应用装配、路由、模型注册、任务注册、Compose、锁文件和测试为证据。
目标结构必须明确标注为目标，不能写成已经完成的当前事实。

建议顺序：
[核心数据流](./data-flow.md) →
[统一模块地图](./module-map.md) →
[系统架构](./overview.md) →
[后端模块边界](./backend-modules.md) /
[前端路由与权限](./frontend-routing.md) →
[ADR](./adr/README.md)。
