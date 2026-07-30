# 深度参考

本目录只保存与当前源码一致、需要较长篇幅说明的领域或引擎参考，不替代源码、
可执行契约、ADR 或运维手册。

- [Ontology 当前实现](./ontology.md)：PostgreSQL `fo_*`、immutable release、
  Mapping、Fact 与查询投影；
- [Sentinel Engine 当前实现](./sentinel-engine.md)：release/dynamic 边界、CDC
  outbox、触发模式、Action/HITL/Webhook。

旧 MongoDB/TuGraph 和特定医疗场景通用设计稿不再作为 active reference；需要
追溯时使用 Git 历史，不在仓库复制一份大体量“归档现状”。实现决策仍须同时
核对当前模型、路由、服务和测试。
