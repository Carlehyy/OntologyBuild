#!/usr/bin/env bash
# AgentLoop 接入准备 —— 在你自己装有阿里云 CLI 的电脑上执行（macOS/Linux）。
#
# 本脚本只在你本机运行，凭据不外传：最终只输出两行需要追加到部署服务器
# /opt/ontologybuild/.env 的配置（LicenseKey 等敏感值由你自行保管）。
#
# 前置条件（一次性）：
#   1. aliyun CLI >= 3.3.15（安装：curl -fsSL https://aliyuncli.alicdn.com/setup.sh | bash）
#   2. cms2 插件可用：aliyun plugin update
#   3. 已配置凭证：aliyun configure（建议使用 RAM 子账号）
#   4. 已开通 AgentLoop 服务
#
# 可用环境变量覆盖默认值：
#   AGENTLOOP_WORKSPACE / AGENTLOOP_REGION / AGENTLOOP_SERVICE_NAME
set -Eeuo pipefail

WORKSPACE="${AGENTLOOP_WORKSPACE:-agentloop-9d2a85cf4ad2319dcd8bbab20b3eed85}"
REGION="${AGENTLOOP_REGION:-cn-hangzhou}"
SERVICE_NAME="${AGENTLOOP_SERVICE_NAME:-ontologybuild-backend}"
OUT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/.runtime"

log() { printf '[onboard] %s\n' "$*"; }

json_get() { # json_get <json> <dot.path>  -> 用 jq 或 python3 取字段
  local json="$1" path="$2"
  if command -v jq >/dev/null 2>&1; then
    printf '%s' "$json" | jq -r ".${path} // empty"
  elif command -v python3 >/dev/null 2>&1; then
    printf '%s' "$json" | python3 -c "
import sys, json
v = json.load(sys.stdin)
for k in '${path}'.split('.'):
    if isinstance(v, dict):
        v = v.get(k)
    elif isinstance(v, list):
        v = v[int(k)]
    else:
        v = None
        break
print(v if v is not None else '')"
  else
    echo "需要 jq 或 python3 解析 JSON" >&2
    exit 1
  fi
}

command -v aliyun >/dev/null 2>&1 || {
  echo "未找到 aliyun CLI，安装方法：https://help.aliyun.com/document_detail/121541.html" >&2
  exit 1
}
ver="$(aliyun version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1 || true)"
[ -n "$ver" ] || { echo "无法读取 aliyun CLI 版本" >&2; exit 1; }
if [ "$(printf '%s\n%s\n' '3.3.15' "$ver" | sort -V | head -1)" != "3.3.15" ]; then
  echo "aliyun CLI 版本过低（当前 ${ver}，需要 >= 3.3.15），请执行 aliyun upgrade" >&2
  exit 1
fi
aliyun cms2 --help >/dev/null 2>&1 || {
  echo "cms2 插件不可用，请执行 aliyun plugin update" >&2
  exit 1
}

log "工作区: ${WORKSPACE}  地域: ${REGION}  应用名: ${SERVICE_NAME}"

log "[1/5] 初始化 APM 配置（幂等，可重复执行）..."
aliyun cms2 apm configuration create --workspace "$WORKSPACE" --region "$REGION"

log "[2/5] 读取 LicenseKey / 上报端点..."
cfg="$(aliyun cms2 apm configuration get --workspace "$WORKSPACE" --region "$REGION" -o json)"
LICENSE_KEY="$(json_get "$cfg" entryPointInfo.authToken)"
PUBLIC_DOMAIN="$(json_get "$cfg" entryPointInfo.publicDomain)"
PROJECT="$(json_get "$cfg" entryPointInfo.project)"
[ -n "$LICENSE_KEY" ] || {
  echo "未能从 APM 配置中读取 LicenseKey，请检查 cms2 权限（建议 RAM 授予 AgentLoop/ARMS 权限）" >&2
  exit 1
}

log "[3/5] 注册应用服务（若提示已存在可忽略）..."
aliyun cms2 apm service create --workspace "$WORKSPACE" --region "$REGION" \
  --body "{\"serviceName\":\"${SERVICE_NAME}\",\"serviceType\":\"TRACE\",\"attributes\":[{\"key\":\"language\",\"value\":\"python\"},{\"key\":\"framework\",\"value\":\"openai\"}]}" \
  || log "（忽略）服务可能已注册，稍后统一校验"

log "[4/5] 拉取 ai-openai 插件接入模板（供后续校准探针配置用）..."
mkdir -p "$OUT_DIR"
if aliyun cms2 integration addon get --addon-name ai-openai --env-type Client -o json \
  >"$OUT_DIR/ai-openai-addon.json" 2>/dev/null; then
  log "模板已保存到 agentloop/.runtime/ai-openai-addon.json（已被 .gitignore 排除）"
else
  log "（可选）插件模板获取失败，不影响接入，可稍后重试"
fi

log "[5/5] 校验服务注册..."
aliyun cms2 apm service list --workspace "$WORKSPACE" --service-name "$SERVICE_NAME" --region "$REGION" -o json

cat <<EOF

========== 接入准备完成，只差最后两步 ==========
1) 把下面两行追加到部署服务器的 /opt/ontologybuild/.env（不要提交进代码仓库）：

ARMS_LICENSE_KEY=${LICENSE_KEY}
ARMS_REGION_ID=${REGION}

2) 在服务器上正常部署一次（GitHub Actions 推送部署或手动 bash deploy/deploy-prod.sh），
   deploy-prod.sh 检测到 ARMS_LICENSE_KEY 后会自动启用探针版 backend。
   部署完成后 2-3 分钟内数据出现在 AgentLoop 控制台。

上报端点：${PUBLIC_DOMAIN}
APM 项目：${PROJECT}
SLS 项目（审计/评估数据，控制台按需关联）：agentloop-cms-9d2a85cf4ad2319dcd8bbab20b3eed85
EOF
