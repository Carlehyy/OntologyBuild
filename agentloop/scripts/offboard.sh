#!/usr/bin/env bash
# AgentLoop 接入的回退 / 卸载。
#
# 用法：
#   bash agentloop/scripts/offboard.sh             # 打印回退步骤
#   bash agentloop/scripts/offboard.sh --delete    # 额外删除控制台服务记录（需 aliyun CLI 凭证）
set -Eeuo pipefail

cat <<'EOF'
========== 回退步骤（按顺序执行） ==========
1) 从部署服务器 /opt/openontology/.env 删除这两行（或清空值）：
     ARMS_LICENSE_KEY=...
     ARMS_REGION_ID=...
2) 正常部署一次（GitHub Actions 或手动 bash deploy/deploy-prod.sh）。
   deploy-prod.sh 检测不到 ARMS_LICENSE_KEY 后自动恢复原版 backend 镜像（无探针），
   部署行为回到接入前。
3) 探针停止上报 3-5 分钟后，可删除控制台里的服务记录（可选，见下）。
EOF

if [ "${1:-}" = "--delete" ]; then
  WORKSPACE="${AGENTLOOP_WORKSPACE:-agentloop-9d2a85cf4ad2319dcd8bbab20b3eed85}"
  REGION="${AGENTLOOP_REGION:-cn-hangzhou}"
  SERVICE_NAME="${AGENTLOOP_SERVICE_NAME:-openontology-backend}"
  echo
  echo "当前注册的服务记录："
  aliyun cms2 apm service list --workspace "$WORKSPACE" --service-name "$SERVICE_NAME" --region "$REGION" -o json
  echo
  echo "确认删除：从上面的输出里找到 serviceId，然后执行："
  echo "  aliyun cms2 apm service delete --workspace '$WORKSPACE' --region '$REGION' --service-id <serviceId>"
fi
