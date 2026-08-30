"""Guard: AGENTS.md §1 domain registry covers every HTTP-exposing backend package.

编码 2026-08-30 agent 友好性审计结论（H2）：backend/app 下凡对外挂载 HTTP
路由的包，必须登记进根 AGENTS.md 第 1 节业务域表，否则该表作为 agent 与
新人的代码发现入口即失真。新增业务域包时必须同步更新 AGENTS.md 第 1 节。
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
APP_DIR = REPO_ROOT / "backend" / "app"

# 迁移期兼容层目录：不按业务域登记，也不是 canonical 落点。
_COMPAT_LAYER_DIRS = {"routers", "models", "schemas", "services"}


def _packages_exposing_http() -> set[str]:
    packages: set[str] = set()
    for child in sorted(APP_DIR.iterdir()):
        if not child.is_dir() or child.name.startswith("__"):
            continue
        if child.name in _COMPAT_LAYER_DIRS:
            continue
        has_router_file = any(p.name == "router.py" for p in child.rglob("router.py"))
        has_routers_dir = any(
            p.is_dir() and p.name == "routers" for p in child.rglob("*")
        )
        if has_router_file or has_routers_dir:
            packages.add(child.name)
    return packages


def test_every_http_exposing_package_is_registered_in_agents_domain_table():
    agents_md = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    missing = sorted(
        name
        for name in _packages_exposing_http()
        if f"backend/app/{name}/" not in agents_md
    )
    assert not missing, (
        "backend/app 下存在对外挂载 HTTP 路由但未登记进根 AGENTS.md 第 1 节"
        f"业务域表的包：{missing}。请同步更新 AGENTS.md 第 1 节（含能力命名），"
        "该表是 agent 与新人定位后端实现的代码发现入口。"
    )
