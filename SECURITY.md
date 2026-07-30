# 安全策略

## 报告安全问题

请私下联系仓库管理员，不要在公开 Issue、PR、日志或聊天中粘贴真实凭据、
生产地址、用户数据或可复现攻击所需的秘密信息。

报告应包含受影响版本、风险、最小复现和建议缓解方式，但必须对 token、
密码、Cookie、证书及个人数据进行脱敏。

## 凭据规则

- 除仓库所有者已批准暂时保留的现有 `production.dependencies.env` 外，仓库
  只允许 `.example` 配置和无效示例值。
- 当前自动部署继续读取该已跟踪文件，以保持现有 push 部署前置条件；不得在
  PR、Issue、日志、聊天或测试报告中回显其内容。
- 后续改为逐项 GitHub Environment Secrets/Variables 或服务器秘密文件时，
  必须作为独立运维变更完成迁移、预检和回滚验证。
- 发现临时例外之外的新凭据进入 Git 后，先轮换，再删除当前文件，最后单独
  协调历史清理。
- CI 日志和测试失败信息不得回显驱动异常详情中的连接串或秘密值。

无秘密的迁移模板为
[`production.dependencies.example.env`](./production.dependencies.example.env)；
它不是当前自动部署事实源。
