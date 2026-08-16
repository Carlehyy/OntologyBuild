# AgentLoop 接入（本体助手观测 / 评估 / 数据飞轮）

本项目根目录下的自包含接入方案：把 OpenOntology 的**本体助手**（Super Assistant，
`backend/app/super_assistant/`）接入阿里云 **AgentLoop**（Agent 观测与优化），实现：

> 观测 → 评估 → 挖 Bad Case → 优化 prompt / 技能 / 工具 → 回归验证 的**数据飞轮**。

接入方式是阿里云官方针对「自研 Python Agent + 原生 OpenAI SDK」推荐的无侵入探针方案
（`aliyun-bootstrap` 探针 + `ai-openai` 插件，自动埋点 LLM 调用 / 工具调用 / Agent 链路），
**不改任何业务代码**。

## 目录结构

```
agentloop/
├── README.md                    # 本文件：接入与使用指南
├── Dockerfile                   # 探针版 backend 镜像（= backend/Dockerfile + 探针安装）
├── compose.agentloop.yml        # docker-compose.prod.yml 的覆盖层（backend 服务）
├── docs/
│   └── evaluators-and-flywheel.md  # 评估器设计、Bad Case 挖掘、数据飞轮 SOP
└── scripts/
    ├── onboard.sh               # 接入准备（在你自己装有 aliyun CLI 的电脑上执行）
    └── offboard.sh              # 回退 / 卸载
```

## 工作原理

1. `agentloop/Dockerfile` 在标准 backend 镜像构建流程的基础上，额外安装阿里云 Python
   探针 `aliyun-bootstrap`（装进 `/app/.venv` 虚拟环境），产出
   `ontologybuild-backend-agentloop:local` 镜像。
2. `agentloop/compose.agentloop.yml` 是 `docker-compose.prod.yml` 的覆盖层，只替换
   backend 服务的三样东西：镜像/构建、启动命令（包一层 `aliyun-instrument`）、
   探针环境变量（`ARMS_*`）。
3. `deploy/deploy-prod.sh` 检测到服务器 `/opt/ontologybuild/.env` 中配置了
   `ARMS_LICENSE_KEY` 时，自动把覆盖层叠加到 compose 文件列表；**没有配置时部署
   行为与原来完全一致**。因此启用/回退只需增删 `.env` 两行，无需改 CI、无需改业务。

## 接入步骤（三步）

### 第 1 步：本地跑 onboard.sh（需要你的阿里云账号，凭据不外传）

在你自己装有阿里云 CLI 的电脑（Mac 即可）上：

```bash
# 前置条件（一次性）：
#   a) 安装 aliyun CLI（>= 3.3.15）: https://help.aliyun.com/document_detail/121541.html
#      curl -fsSL https://aliyuncli.alicdn.com/setup.sh | bash
#   b) 确保 cms2 插件可用:  aliyun plugin update
#   c) 配置凭证（建议 RAM 子账号）:  aliyun configure

bash agentloop/scripts/onboard.sh
```

脚本会依次完成：初始化 APM 配置（幂等）→ 读取 LicenseKey/上报端点 → 注册应用服务
`ontologybuild-backend` → 拉取 `ai-openai` 插件模板 → 校验注册结果，最后打印两行
需要追加到服务器 `.env` 的配置（形如 `ARMS_LICENSE_KEY=…`）。

> LicenseKey 是敏感凭据：只保留在你自己的终端输出里，**不要提交进仓库**，
> 仓库转私有不改变这一点。

### 第 2 步：服务器 .env 追加两行

SSH 到部署服务器，把 onboard.sh 输出的两行追加到 `/opt/ontologybuild/.env`：

```bash
ARMS_LICENSE_KEY=<onboard.sh 输出的值>
ARMS_REGION_ID=cn-hangzhou
```

### 第 3 步：正常部署（自动启用探针）

无需任何额外操作：下次部署（GitHub Actions 推送部署，或服务器上手动
`bash deploy/deploy-prod.sh`）会自动构建探针版镜像并用探针包装启动命令。
部署完成后 2–3 分钟内，数据出现在 AgentLoop 控制台。

## 验证接入

1. 服务注册：控制台「接入中心/应用列表」能看到 `ontologybuild-backend`；
   或用 CLI：`aliyun cms2 apm service list --workspace <workspace> --region cn-hangzhou`。
2. 数据上报：控制台「观测」里出现 Trace（LLM 调用耗时、Token 用量、工具调用）。
3. 没有数据时的排查顺序：
   - `docker compose -f docker-compose.prod.yml -f agentloop/compose.agentloop.yml logs backend` 中探针启动日志；
   - 确认容器内环境变量生效：`docker compose … exec backend env | grep ARMS`；
   - 服务器能否出网访问上报端点（onboard.sh 会打印 `publicDomain`）；
   - 若 uvicorn 包装不生效（阿里云文档的 uvicorn 特例），备选方案是在
     `app/main.py` 首行导入 `from aliyun.opentelemetry.instrumentation.auto_instrumentation import sitecustomize`（需要改业务代码，仅作兜底）。

## 接入之后怎么用（数据飞轮）

观测只是第一步。评估器怎么建、Bad Case 怎么挖、周度优化闭环怎么跑，见
[`docs/evaluators-and-flywheel.md`](docs/evaluators-and-flywheel.md)。

要点速览：

- **观测**：看每个会话的 Token 消耗热点、工具调用失败/重试、端到端延迟构成
  （模型 vs MCP 工具 vs 记忆检索）。
- **评估**：预置评估器（任务完成度、幻觉、正确性）+ 自定义「本体构建正确性」评估器；
  本体助手自主模式自带的 `[GOAL_COMPLETE]`/`[GOAL_FAILED]` 标记是现成的任务完成度标签。
- **挖 Case**：Trace2Dataset Pipeline 自动产出 BadCase/Golden 数据集。
- **优化**：改 system prompt / 技能包（`backend/app/super_assistant/skill_store.py`）/
  上下文压缩阈值 / 模型选择，Playground 实验对比后上线，持续抽样评估做回归。

## 回退 / 卸载

```bash
# 1) 从服务器 .env 删除 ARMS_LICENSE_KEY、ARMS_REGION_ID 两行
# 2) 正常部署一次（恢复原版 backend 镜像，无探针）
bash agentloop/scripts/offboard.sh          # 打印完整回退步骤（可选：--delete 删除控制台服务记录）
```

## 安全与成本

- **凭据**：LicenseKey 只存在于服务器 `.env`（该文件本来就不进仓库）；本目录下
  `.runtime/` 是 onboard.sh 的本地产物目录，已被 `.gitignore` 排除。
- **数据合规**：线上对话内容、工具参数会经探针上报阿里云。AgentLoop 支持端侧脱敏
  （手机号/身份证/邮箱/IP/银行卡），默认关闭，建议在控制台按需开启。
- **计费**：按量后付费——AI 积分 0.01 元/积分（一次评估约 10 积分）、执行 0.001 元/次、
  数据集存储 0.00004 元/条/天；新用户首月有免费额度。可观测依赖 ARMS、审计/评估数据
  依赖 SLS，另按各自计费规则收费。详情见
  [计费说明](https://help.aliyun.com/zh/document_detail/3044490.html)。

## 维护注意

`agentloop/Dockerfile` 与 `backend/Dockerfile` 的构建步骤一一对应（仅追加探针安装）。
`backend/Dockerfile` 变更（如升级 uv 版本、基础镜像）时，请同步本文件对应行。

参考文档：
[选择 AI Agent 接入方式](https://help.aliyun.com/zh/document_detail/3049365.html) ·
[什么是 AgentLoop](https://help.aliyun.com/zh/document_detail/3033860.html) ·
[评估概述](https://help.aliyun.com/zh/document_detail/3042179.html) ·
[常见问题](https://help.aliyun.com/zh/document_detail/3042703.html)
