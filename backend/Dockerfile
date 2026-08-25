ARG PYTHON_BASE_IMAGE=python:3.12-slim
FROM ${PYTHON_BASE_IMAGE}

COPY --from=ghcr.io/astral-sh/uv:0.11.14 /uv /uvx /bin/
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
# 助手评估（OpenJudge）：py-openjudge 声明的全量依赖（pandas<3 / openai>=2.8 /
# nltk / sklearn / dashscope 等）与平台既有锁定版本冲突，但其评分器子集在运行时
# 只需要 loguru/pydantic/tqdm/openai 等轻量包。因此以 --no-deps 安装本体，
# 轻量依赖显式进入 pyproject；版本精确锁定保证可复现。
RUN uv pip install --python /app/.venv/bin/python --no-deps py-openjudge==0.2.1
COPY . .
