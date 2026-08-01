# Windows 回环代理绕行、配置中心门禁与默认值优化

> 本文保留 2026-07-31 当时的实施证据；其中 Chroma 可选增强和本地依赖门禁
> 结论已由[稳定版必需运行依赖与降级移除](./2026-08-02-required-runtime-dependencies.md)
> 取代，不再代表当前启动契约。

| 字段 | 内容 |
|---|---|
| 状态 | Validated |
| 日期 | 2026-07-31 |
| 负责人 | Codex |
| 评审人 | 未单独指定 |
| Issue/PR | 直接提交，无独立 Issue/PR |
| Commit | 本记录所在提交 |
| 目标分支 | `nano-ontoprompt` |
| 业务域 | 本地配置中心、系统设置、数据管家、平台基础设施 |

## 背景

Windows 11 即使没有设置 `HTTP_PROXY`、`HTTPS_PROXY`，Python 仍可能从注册表
读取系统代理。HTTPX 0.27.2 和 `urllib.request` 的默认客户端因此可能把
`127.0.0.1`、`localhost` 或 `::1` 上的 Chroma、CDP、n8n、后端和前端探针
发送给公司代理，造成“服务实际正常但配置测试失败”，并可能把 n8n API Key
发送到不应接收它的代理边界。

配置中心原默认监听 `127.0.0.1:8765`，与本地其他工具存在端口冲突风险。

配置中心还把 Chroma 和默认大模型与 PostgreSQL、Redis/Celery、Neo4j、
MinIO、Chromium CDP、n8n 一并设为生成配置的硬门槛。代码核对确认 Chroma
确实被语义检索、知识抽取和映射投影使用，但其客户端在不可用时会安全降级；
默认大模型也不是后端核心启动条件。因此两者应保留为可选增强，而不是阻止
本地平台启动。

## 目标

- 回环 HTTP 请求强制直连；
- 远程 LLM、n8n、Chroma 等请求继续使用操作系统代理和环境 CA；
- 配置测试与平台实际调用采用一致策略；
- 配置中心默认端口调整为 `8888`，保留环境变量覆盖能力；
- 六项核心依赖作为生成配置的连通性门禁，Chroma 和默认模型保留为可选测试；
- 启动时预填用户提供的非敏感固定参数，并支持 Git 忽略的本机敏感默认值；
- 用自动化测试证明本地凭据不会进入代理。

## 非目标

- 不更改生产后端、前端、n8n、CDP 或 Chroma 的业务端口；
- 不全局设置 `NO_PROXY`，也不全局关闭 `trust_env`；
- 不改变外部服务 URL、TLS 校验或凭据格式；
- 不升级锁定的 Chroma 版本。
- 不把用户密码、API Key 或其他真实凭据提交到 Git。

## 当前状态与变更前基线

- `config/app/probes.py` 与运行复检使用默认 `httpx.Client()`；
- n8n 业务客户端使用默认 HTTPX 代理继承；
- CDP 与依赖就绪检查使用默认 `urllib.request.urlopen()`；
- Chroma 0.5.20 在内部创建 HTTPX 会话且不暴露 transport 参数；
- 配置中心默认端口为 `8765`。
- 配置生成要求八项探针全部通过，空的默认模型 API Key 会让后端本地配置同步
  直接启动失败。

## 变更范围

| 模块/路径 | 改动 | API/menu key/数据库/Celery/环境变量影响 |
|---|---|---|
| `config/app/http_transport.py` | 新增 HTTPX 回环直连 mounts | 无 API、数据库或任务影响 |
| `config/app/probes.py`、`main.py` | 探针和运行复检接入选择性代理策略 | `OPENONTOLOGY_CONFIG_PORT` 名称不变，默认值改为 `8888` |
| `backend/app/shared/http_transport.py` | HTTPX 与标准库的回环直连策略 | 无公开契约变化 |
| n8n、CDP、健康检查 | 正式调用链使用同一策略 | URL、请求头和响应结构不变 |
| `backend/app/shared/chroma_service.py` | 为锁定的 Chroma 0.5.20 注入回环 mounts | Chroma API 和集合行为不变 |
| 配置门禁与页面 | PostgreSQL、Redis/Celery、Neo4j、MinIO、CDP、n8n 为必选；Chroma、LLM 为可选 | bootstrap 增加 `optional_services`、`defaults_loaded` |
| `config/app/env_file.py`、`defaults.env.example` | 支持忽略的本机默认值覆盖，敏感值仍遮蔽 | 新增 `config/generated/local/defaults.env` 本机约定 |
| `backend/app/services/local_config_sync.py` | 空 LLM API Key 时只同步必选 n8n，不创建默认模型 | 已配置 LLM 的原有同步行为不变 |
| 测试与文档 | 增加代理、密钥隔离和端口回归 | 无运行时数据影响 |

## 兼容策略

HTTPX 保持 `trust_env=True` 的默认语义，只为三个标准回环主机及实际配置的
回环 IP 增加直连 mount。标准库请求使用选择性 ProxyHandler：初始回环请求
直连，非回环目标及可能的跨主机重定向仍保留系统代理。

Chroma 0.5.20 没有 transport 注入参数，因此只在创建回环 Chroma 客户端的
临界区替换 Chroma 模块内的 HTTPX 引用，客户端创建后立即恢复；远程 Chroma
不经过该适配。

用户提供的 PostgreSQL、Redis、Neo4j 和 MinIO 主机、端口、账号及数据库名
作为非敏感默认值提交。真实密码和 API Key 只能写入被 Git 忽略的
`config/generated/local/defaults.env`；配置中心在正式 `.env` 尚不存在时读取，
并继续通过服务端保留机制避免把敏感值回传到浏览器。

## 安全与数据处理

- n8n API Key 仅发送给目标回环服务；
- 不记录、复制或回显任何真实凭据；
- 附件中的真实密码不进入源码、示例文件、测试、提交或迭代记录；
- 不修改进程级代理环境变量，避免并发请求受到瞬时污染；
- 远程 HTTPS 仍使用既有系统代理和 CA 信任配置。

## 验收条件

- 配置中心默认监听 `127.0.0.1:8888`；
- 回环 HTTPX 请求选择直连 transport，远程请求选择代理 transport；
- 标准库回环请求使用选择性代理绕行；
- n8n API Key 到达本机 n8n，模拟代理接收请求数为零；
- 只需六项必选探针即可生成配置，Chroma 和 LLM 仍可单独测试；
- 空 LLM API Key 不阻止后端本地配置同步；
- 本机默认值文件的敏感字段不回传浏览器，且不会被 Git 跟踪；
- 配置中心完整单测和后端相关回归通过。

## 验证证据

| 层级 | 实际命令/环境 | 退出结果 | CI URL / artifact / 跳过原因 |
|---|---|---|---|
| 配置中心 | `cd config && uv run pytest -q` | 41 passed | 本地隔离环境 |
| 配置页面静态资源 | `node --check config/static/app.js`；Python HTML parser | 通过 | JS 语法与 HTML 解析 |
| 后端代理公共策略 | `pytest --confcutdir=tests/shared tests/shared/test_http_transport.py tests/shared/test_chroma_proxy.py` | 7 passed | 本地最小检出，设置 `PYTHONPATH=.` |
| n8n 调用链 | `pytest --confcutdir=tests/settings tests/settings/test_n8n_client.py` | 4 passed | 包含真实本地 HTTP 服务和模拟代理 |
| 可选 LLM 启动路径 | 隔离依赖的 `sync_local_managed_runtime_config` 最小执行验证 | 通过 | 确认空 Key 只写入 n8n 且不查询模型管理员 |
| 语法 | `python -m py_compile` 覆盖六个受影响后端模块 | 通过 | 本地隔离环境 |
| 完整后端/前端 | 未在本地执行 | 当前工作区仅检出受影响源码和配置页面静态资源 | 由分支 CI 使用完整仓库执行 |
| stack/external E2E | 未执行 | 当前环境没有用户的 n8n、CDP、Chroma 与 Windows 注册表代理 | 上线前在 Windows 本机验证 |

## 上线步骤、监控指标与观察窗口

正常部署后，在 Windows 启动 `config/start.bat`，确认页面地址为
`http://127.0.0.1:8888/`，执行六项必选测试并生成配置；按需单独测试 Chroma
和默认模型，然后执行完整启动复检。
观察首次配置和至少一次后端重启，关注连接耗时、代理通知页、n8n 401/403、
Chroma heartbeat 与浏览器就绪状态。

## 回滚触发条件与逐步方案

若远程服务代理失效、回环服务仍进入代理，或 Chroma 客户端初始化出现新异常，
立即回滚本记录所在提交并恢复配置中心默认端口。回滚不涉及数据库、迁移或
持久数据；如用户已改用 `8888` 的快捷方式，只需同步恢复地址或显式设置
`OPENONTOLOGY_CONFIG_PORT`。

## 已知风险与后续动作

Chroma 的适配依赖当前锁定的 0.5.20 内部模块边界；未来升级 Chroma 时必须
先运行代理专项测试，并优先替换为上游正式 transport 注入能力。Windows
注册表代理的最终验收需要在目标 Windows 11 机器执行。
