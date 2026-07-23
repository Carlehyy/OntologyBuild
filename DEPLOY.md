# OntoBuild 源码启动部署指南（无 Docker · 公网隧道）

本指南适用于在**无 Docker 环境**下从源码启动 OntoBuild 项目，并通过 cloudflared 隧道提供公网预览地址。

---

## 前置条件

- Python 3.11+
- Node.js 20+ / npm
- Git
- 能访问外网（用于下载依赖和建立隧道）

---

## 1. 拉取代码

```bash
git clone --branch nano-ontoprompt https://github.com/Carlehyy/OntologyBuild.git
cd OntologyBuild
```

---

## 2. 后端

### 2.1 创建虚拟环境并安装依赖

```bash
python3 -m venv /tmp/ob-venv
/tmp/ob-venv/bin/pip install -r backend/requirements.txt
/tmp/ob-venv/bin/pip install networkx
```

> `networkx` 为上游 `requirements.txt` 遗漏的依赖，需手动补充。

### 2.2 配置环境变量

在 `backend/` 目录下创建 `.env`：

```ini
ENVIRONMENT=development
DATABASE_URL=sqlite:////tmp/ontoprompt.db
SECRET_KEY=<替换为随机字符串>
FIRST_ADMIN_USER=admin
FIRST_ADMIN_PASSWORD=<替换为安全密码>
UPLOADS_DIR=./uploads
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=<替换>
MINIO_ENDPOINT=localhost:9000
MINIO_ACCESS_KEY=<替换>
MINIO_SECRET_KEY=<替换>
MINIO_USE_SSL=false
STORAGE_LOCAL_FALLBACK=true
STORAGE_LOCAL_DIR=/绝对路径/ontobuild-object-storage
CHROMA_HOST=localhost
CHROMA_PORT=8001
```

> 当前配置使用 **SQLite** 替代 PostgreSQL。Neo4j / ChromaDB / MinIO 为可选项。
> MinIO 未运行时，附件与数据产物写入 `STORAGE_LOCAL_DIR`；源码部署应把它设为
> 绝对路径并纳入磁盘备份，不能使用 `/tmp`。

### 2.3 创建上传目录并启动

```bash
mkdir -p uploads
cd backend
nohup /tmp/ob-venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 \
  > ../backend.log 2>&1 &
```

验证：

```bash
curl http://localhost:8000/docs
```

---

## 3. 前端

### 3.1 安装依赖

```bash
cd ../frontend

# 确保 npm 安装 devDependencies
export NODE_ENV=development
rm -rf node_modules package-lock.json
npm install
```

### 3.2 配置 Vite 允许隧道域名

编辑 `vite.config.ts`，在 `server` 配置块中添加 `allowedHosts: true`：

```ts
server: {
  host: true,
  port: 5173,
  allowedHosts: true,
  proxy: { '/api': 'http://localhost:8000' }
}
```

### 3.3 启动

```bash
nohup npm run dev -- --host 0.0.0.0 > ../frontend.log 2>&1 &
```

验证：

```bash
curl http://localhost:5173/
```

---

## 4. 公网隧道

### 4.1 下载 cloudflared

```bash
curl -sSL -o /tmp/cloudflared \
  https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64
chmod +x /tmp/cloudflared
```

### 4.2 启动隧道

```bash
nohup /tmp/cloudflared tunnel --no-autoupdate --protocol http2 \
  --url http://localhost:5173 > tunnel.log 2>&1 &
```

> `--protocol http2` 是必要的：默认 QUIC 走 UDP，部分容器/网络环境会阻断 UDP，导致隧道无法建立。

### 4.3 获取公网地址

```bash
grep -oP 'https://[a-z0-9-]+\.trycloudflare\.com' tunnel.log | head -1
```

---

## 5. 端口概览

| 组件 | 端口 | 说明 |
|------|------|------|
| 后端 API | `8000` | FastAPI，含 Swagger 文档 `/docs` |
| 前端界面 | `5173` | Vite dev server，`/api` 反代到后端 |
| 公网隧道 | 动态域名 | cloudflared quick tunnel（trycloudflare.com） |

---

## 常见问题

| 现象 | 原因 | 解决 |
|------|------|------|
| `vite: not found` | `NODE_ENV=production` 导致 devDependencies 未安装 | `export NODE_ENV=development` 后重装 |
| `ModuleNotFoundError: networkx` | 上游依赖遗漏 | `pip install networkx` |
| 隧道 URL 返回 403（Vite 拦截） | Vite 8 默认拦截非白名单 Host | `vite.config.ts` 加 `allowedHosts: true` |
| 隧道日志反复 QUIC 报错、不提供 URL | UDP 被阻断 | 启动时加 `--protocol http2` |

---

## GitHub Actions 自动部署（Docker · 云服务器）

本仓库已新增 GitHub Actions 自动部署流程：每次 push 到 `nano-ontoprompt` 分支都会触发部署到云服务器。

### GitHub Secrets

在仓库 `Settings -> Secrets and variables -> Actions -> New repository secret` 中配置：

| Secret | 示例 | 说明 |
|---|---|---|
| `DEPLOY_HOST` | `64.90.17.41` | 云服务器公网 IP |
| `DEPLOY_USER` | `root` | SSH 用户 |
| `DEPLOY_PASSWORD` | `<服务器 SSH 密码>` | SSH 密码 |
| `DEPLOY_APP_DIR` | `/opt/ontologybuild` | 服务器部署目录，可选 |
| `DEPLOY_HEALTH_URL` | `http://64.90.17.41/` | 部署后公网健康检查地址，可选 |

### 服务器要求

服务器需要安装：

- Docker
- Docker Compose plugin（即 `docker compose`）
- 80 端口对公网开放

### 部署流程

`.github/workflows/deploy-nano-ontoprompt.yml` 会执行：

1. checkout 当前提交；
2. 使用 `docker-compose.prod.yml` 在 GitHub Actions 中构建一次镜像，提前暴露 Dockerfile/依赖错误；
3. 打包当前源码并通过 SSH/SCP 上传到服务器；
4. 在服务器执行 `scripts/deploy-prod.sh`；
5. 服务器重新 build 镜像并 `docker compose up -d --remove-orphans`；
6. 访问 `DEPLOY_HEALTH_URL`，失败则 Actions 失败。

### 镜像策略

当前策略是：**每次提交都重新构建镜像并部署**。

这样可以保证线上容器一定对应当前分支最新提交，避免因变更判断错误导致漏部署。后续如果构建耗时明显，可以再改成按 `backend/`、`frontend/`、compose 文件变更来选择性构建。

### 公网访问

生产前端使用 `frontend/Dockerfile.prod` 构建静态资源，并由 Nginx 在容器内监听 80 端口。`docker-compose.prod.yml` 默认映射：

```yaml
ports:
  - "${PUBLIC_PORT:-80}:80"
```

因此默认公网访问地址是：

```text
http://64.90.17.41/
```

如需改端口，在服务器 `.env` 中设置 `PUBLIC_PORT`，并同步更新 `DEPLOY_HEALTH_URL`。

### 外部 n8n 与附件文件网关

数据管家不会把 MinIO 长期凭据交给 n8n；每次执行只注入短时、流水线范围内的上传令牌。
n8n 必须能够访问服务器 `.env` 中的文件网关地址：

```text
PIPELINE_FILE_GATEWAY_BASE_URL=https://平台域名/api/v2/file-transfer
PIPELINE_FILE_PUBLIC_APP_BASE_URL=https://平台域名
PIPELINE_FILE_PUBLIC_API_BASE_URL=https://平台域名
```

自动部署会在该变量缺失或仍为 `http://backend:8000/...` 默认值时，根据
`DEPLOY_HEALTH_URL` 自动写入可达地址。若 n8n 不在平台 Docker 网络中，不能继续使用
`backend` 服务名。公网文件传输必须配置 HTTPS；HTTP 仅适合临时验收。

这三个地址职责不同：`PIPELINE_FILE_GATEWAY_BASE_URL` 仅供 n8n 上传产物；
`PIPELINE_FILE_PUBLIC_APP_BASE_URL` 用于生成“先登录、再继续下载”的前端 Hash 深链；
`PIPELINE_FILE_PUBLIC_API_BASE_URL` 用于生成匿名分享 API。后两项必须是其他电脑浏览器
可访问的 HTTP(S) origin，不要包含路径、账号、查询参数或 `#` 片段。它们可以使用不同
域名，例如前端为 `https://platform.example.com`、API 为
`https://api.example.com`，但相应反向代理和 CORS 也必须放行。

部署脚本会在后两项为空或仍为本地开发默认值时，将它们设置为去掉尾部 `/` 的
`DEPLOY_HEALTH_URL`；显式公网配置不会被覆盖。公网分享建议始终使用 HTTPS，HTTP 只适合
当前 IP 验收或受控内网。

MinIO 可用时新对象返回 `s3://bucket/key`，不可用时返回
`local://bucket/key`。两者都是平台内部存储标识；对外下载仍统一经过平台的鉴权下载或
匿名分享接口。`docker-compose.prod.yml` 将 API 与 Celery 的本地对象目录统一指向
`/uploads/object-storage`，并挂载同一个 `uploads` 持久卷，因此 MinIO 恢复后既有
`local://` 附件仍可读取。

生产栈默认仍会启动 MinIO。明确不接入 MinIO 时，可以只启动其余服务：

```bash
docker compose -f docker-compose.prod.yml up -d \
  db redis neo4j chromadb browser backend celery_worker frontend
```

后端不再以 MinIO 健康状态作为启动前提。若自行编排容器，必须同时为 backend 和
celery_worker 挂载同一持久卷到 `/uploads`，并保持：

```ini
STORAGE_LOCAL_FALLBACK=true
STORAGE_LOCAL_DIR=/uploads/object-storage
```
