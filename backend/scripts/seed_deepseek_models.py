"""注册/更新 DeepSeek 模型配置（幂等，可重复运行）。

用法（必须在 backend 目录下运行，确保读到 .env 的 SECRET_KEY）：
    .venv/bin/python scripts/seed_deepseek_models.py <api_key>

- DeepSeek V4 Pro   → 默认对话模型（探索/草稿/助手兜底全用它）
- DeepSeek V4 Flash → 轻量模型（可在探索页手动切换）
两者均为 OpenAI 兼容协议（provider=openai + api_base）。
"""
import sys

from app.database import SessionLocal
from app.model_configs.models import ModelConfig
from app.shared.encryption import encrypt
from app.models.user import User

API_BASE = "https://api.deepseek.com"
MODELS = [
    {"name": "DeepSeek V4 Pro", "model": "deepseek-v4-pro", "is_default": True},
    {"name": "DeepSeek V4 Flash", "model": "deepseek-v4-flash", "is_default": False},
]


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("用法: seed_deepseek_models.py <api_key>")
    api_key = sys.argv[1].strip()

    db = SessionLocal()
    try:
        user = (db.query(User).filter(User.role == "admin").first()
                or db.query(User).first())
        if not user:
            raise SystemExit("users 表为空 —— 先在平台注册一个账号再运行本脚本")

        for spec in MODELS:
            row = (db.query(ModelConfig)
                   .filter(ModelConfig.name == spec["name"]).first())
            if row is None:
                row = ModelConfig(name=spec["name"], created_by=user.id)
                db.add(row)
            row.config_type = "llm"
            row.provider = "openai"          # OpenAI 兼容协议（llm_bridge openai 分支）
            row.api_base = API_BASE
            row.api_key_encrypted = encrypt(api_key)
            row.models = [spec["model"]]
            row.enabled = True
            row.is_default = spec["is_default"]
            row.options = dict(row.options or {})
            print(f"✓ {spec['name']}  models={row.models}  default={row.is_default}")
        db.commit()
        print("完成。默认模型: deepseek-v4-pro（探索/文档/草稿均走它，除非前端指定）")
    finally:
        db.close()


if __name__ == "__main__":
    main()
