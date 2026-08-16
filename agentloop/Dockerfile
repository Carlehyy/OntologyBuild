# AgentLoop（阿里云 AgentLoop 观测）探针版 backend 镜像。
#
# 与 backend/Dockerfile 的构建步骤一一对应（仅追加探针安装），
# 由 agentloop/compose.agentloop.yml 作为 docker-compose.prod.yml 的覆盖层启用。
# backend/Dockerfile 变更（基础镜像、uv 版本等）时需同步本文件对应行。
#
# 参考：https://help.aliyun.com/zh/cms/cloudmonitor-2-0/manually-install-the-python-agent
ARG PYTHON_BASE_IMAGE=python:3.12-slim
FROM ${PYTHON_BASE_IMAGE}

COPY --from=ghcr.io/astral-sh/uv:0.11.14 /uv /uvx /bin/
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/ .

# ---- AgentLoop Python 探针（自研探针，自动埋点 OpenAI SDK 等 LLM 调用）----
# 构建时按地域安装探针；LicenseKey 等敏感凭据只通过运行时环境变量注入，
# 不写入镜像。
ARG ARMS_REGION_ID=cn-hangzhou
RUN uv pip install --python /app/.venv/bin/python aliyun-bootstrap \
 && ARMS_REGION_ID=${ARMS_REGION_ID} /app/.venv/bin/aliyun-bootstrap -a install
