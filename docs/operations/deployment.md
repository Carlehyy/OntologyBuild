# GitHub Actions 自动部署

## 触发边界

当前生产部署由 `.github/workflows/deploy-nano-ontoprompt.yml` 管理：

- push 到 `nano-ontoprompt`；
- 手工 `workflow_dispatch`。

目录治理和功能开发必须通过功能分支与 PR，不得直接向该分支试错。

## 当前流程

1. 使用 Python 3.12 和各自 `uv.lock` 执行后端、配置中心回归；
2. 运行 Alembic 新库升级与单 head 检查；
3. 使用当前已跟踪的生产依赖清单验证部署配置；
4. 执行文档链接、目录索引与仓库卫生守卫；
5. 执行前端单元测试、feature boundary、E2E 分类、lint、生产构建和离线
   Playwright 回归；
6. 构建生产镜像，将本次作业扫描到的 SSH host key 写入 `known_hosts` 并强制
   校验，同时在任何远端命令前校验部署目录；
7. 将当前版本中的 `production.dependencies.env` 作为受控部署输入；
8. 通过受测试的运行时白名单生成部署包并上传；服务器部署入口再次校验目录后
   执行迁移和 Compose 启动；
9. 检查 API readiness、Celery worker 和前端静态资源；
10. 无论成功失败，清理 runner 上的上传压缩包。

PR 到 `nano-ontoprompt` 时，独立的 `.github/workflows/ci.yml` 会并行执行
文档/仓库卫生、后端、配置中心和前端门禁，但不会执行部署。

生产 Nginx 对 `/api/`、`/api-hub/` 和 `/proxy/` 使用各自的后端代理契约。
独立的 MinIO Streamable HTTP 服务继续通过 `/mcp/minio` 代理到 backend；
已经退役的通用 `/mcp`（以及除 MinIO 外的 `/mcp/` 子路径）必须直接返回
404，不能落入 SPA 的 `index.html`。`scripts/ci/test-deploy-guards.sh` 固定这条
生产路由边界。

## 首次登录与管理员密码恢复

首次部署且服务器尚无 `.env` 时，部署脚本从 `.env.example` 创建持久配置：
初始用户名默认为 `admin`，`FIRST_ADMIN_PASSWORD` 会在服务器上随机生成，
`.env` 权限设为 `0600`，值不会出现在 Actions 日志。上传新版本时，工作流会
先保存并恢复已有 `.env`，因此正常重部署不会重新生成该密码。

部署包由 `scripts/ci/create-deployment-archive.sh` 构建，只包含生产 Compose、
前后端构建/运行输入、Alembic、容器初始化资源、部署入口和受控维护脚本。
`docs/`、测试、fixture、前端 E2E 源码及过程产物不会上传；白名单由
`scripts/ci/test-deploy-guards.sh` 在 PR 和部署验证阶段共同锁定。

首次登录只能在受控的服务器交互终端读取该值。先确认文件权限，再读取所需的
两项；若自定义了 `DEPLOY_APP_DIR`，请替换下列路径。不要把输出复制到聊天、
Issue、PR、工单或 CI 日志：

```bash
cd /opt/ontologybuild
sudo stat -c '%a %U:%G %n' .env
sudo awk -F= '
  $1 == "FIRST_ADMIN_USER" || $1 == "FIRST_ADMIN_PASSWORD" {
    print $1 "=" substr($0, index($0, "=") + 1)
  }
' .env
```

用该随机密码完成首次登录后，应立即在同一个受控终端重置数据库中的密码。
下面通过静默输入避免新密码进入 shell history；用户名如已自定义，替换
`admin`：

```bash
cd /opt/ontologybuild
read -rsp 'New admin password: ' ONTOLOGYBUILD_NEW_ADMIN_PASSWORD
printf '\n'
sudo docker compose -f docker-compose.prod.yml exec -T \
  -e PYTHONPATH=/app backend \
  python - --user admin --password "${ONTOLOGYBUILD_NEW_ADMIN_PASSWORD}" \
  < backend/scripts/maintenance/reset_admin_password.py
unset ONTOLOGYBUILD_NEW_ADMIN_PASSWORD
```

生产 backend 镜像按 `.dockerignore` 不携带人工维护脚本；上述命令从服务器源码
目录把受版本控制的脚本送入正在运行的容器标准输入，并显式使用容器内
`/app` 代码。

该维护脚本目前通过命令参数接收密码，运行期间同机特权用户可能观察进程参数；
因此只能在访问受控的服务器上交互执行，不得封装到 Actions 或把命令输出保存
为 artifact。若初始随机值丢失，无需读取或修改 `.env` 来“同步密码”，直接用
同一维护命令重置目标账号即可。`FIRST_ADMIN_PASSWORD` 只是数据库中没有任何
admin 时的 seed 输入，编辑它不会修改已经存在的管理员密码。

## 当前已知缺口

- 发布前 verify 与 deploy 仍在同一个 workflow，避免绕过发布门禁；
- Actions 构建的镜像没有作为不可变产物推送，服务器会再次构建；
- 服务器部署会替换应用目录；
- 健康检查失败尚无自动镜像回滚；
- SSH 仍使用密码方式；
- SSH host key 在每次临时 runner 上通过 `ssh-keyscan` 建立信任并在后续命令
  强制校验，但尚未用受保护 Secret 固定预期 fingerprint，仍属于 TOFU；
- Playwright `stack`/`external` 真实栈验收尚未成为自动发布门禁。

这些缺口在目录移动前必须逐步关闭。生产部署最终应只消费 CI 构建并签名/标记
的不可变镜像。

## Contract migration 的停机与失败恢复

会删除或收紧旧应用仍在使用的 schema 的 Alembic 迁移属于 contract
migration。此类部署必须先记录升级前正在运行的 backend、Celery worker 和
frontend，再停止这些 runtime，然后才能运行迁移。不得在旧 API/worker
仍可访问数据库时删表。这一顺序意味着 contract migration 有明确的短暂停机窗口。

失败恢复同时以迁移是否到达 head、数据库 revision 是否变化为界：

- 迁移失败、未到达 head，且数据库 revision 与停机前记录完全相同时，才只
  重启本次部署前确实在运行的旧服务；
- revision 无法读取或已经变化（即使还没到 head）时，拒绝自动重启旧服务，
  避免旧二进制连接部分升级的 schema；
- 迁移已成功，但新服务启动或健康检查失败时，禁止把旧应用接到新
  schema。部署脚本会停止任何已部分启动的新版 runtime；必须保持停机，
  恢复部署前同一时间点的数据库与文件备份并部署旧镜像，或执行经批准的
  前向修复。

脚本自动恢复不可代替备份与恢复演练。对删表迁移，Alembic downgrade 只恢复
空 schema，不恢复已删行或物理文件。

## 部署前检查

- 当前版本中的 `production.dependencies.env` 存在且通过只读配置校验；
- 既有 `DEPLOY_HOST`、`DEPLOY_USER`、`DEPLOY_PASSWORD`、
  `DEPLOY_APP_DIR`、`DEPLOY_HEALTH_URL` Repository Secrets 可用；
- `DEPLOY_APP_DIR` 通过 `bash scripts/ci/validate-deploy-app-dir.sh
  "${DEPLOY_APP_DIR:-/opt/ontologybuild}"`；
- 如启用 `STRICT_IMAGE_DIGESTS`，最终服务器 `.env` 的全部镜像引用均已固定
  到不可变 `@sha256`；
- 数据库备份完成且恢复演练有效；
- Alembic 新库与现存库副本升级通过；
- staging 与关键真实 E2E 通过；
- 上一版本镜像、Compose 配置和回滚负责人已确认。

配置清单见 [配置说明](./configuration.md)，回滚见 [回滚说明](./rollback.md)。
