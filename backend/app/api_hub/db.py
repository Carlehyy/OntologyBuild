"""SQLite 持久化。每次操作开一个新连接，单用户本地工具足够安全。"""
import sqlite3

from . import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS interfaces (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT    NOT NULL DEFAULT '未命名接口',
    description   TEXT    NOT NULL DEFAULT '',   -- 给 Agent 看的用途/参数说明（MCP 渐进式披露）
    group_name    TEXT    NOT NULL DEFAULT '',
    method        TEXT    NOT NULL DEFAULT 'GET',
    url           TEXT    NOT NULL DEFAULT '',
    query_params  TEXT    NOT NULL DEFAULT '[]',   -- JSON: [{key,value}]
    headers       TEXT    NOT NULL DEFAULT '[]',   -- JSON: [{key,value}]
    body_type     TEXT    NOT NULL DEFAULT 'none', -- none|json|form|raw
    body_content  TEXT    NOT NULL DEFAULT '',
    use_w3        INTEGER NOT NULL DEFAULT 1,
    mcp_enabled   INTEGER NOT NULL DEFAULT 0,
    open_enabled  INTEGER NOT NULL DEFAULT 0,
    sort_order    INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT    NOT NULL,
    updated_at    TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    interface_id     INTEGER NOT NULL,
    ok               INTEGER NOT NULL DEFAULT 0,
    status_code      INTEGER,
    elapsed_ms       INTEGER,
    request_snapshot TEXT,
    response_headers TEXT,
    response_body    TEXT,
    error            TEXT,
    relogin          INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT    NOT NULL,
    FOREIGN KEY (interface_id) REFERENCES interfaces(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(_SCHEMA)
        _migrate(conn)


def _migrate(conn) -> None:
    """对老版本 app.db 做无损升级：缺列就补。"""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(interfaces)").fetchall()}
    if "mcp_enabled" not in cols:
        conn.execute("ALTER TABLE interfaces ADD COLUMN mcp_enabled INTEGER NOT NULL DEFAULT 0")
    if "description" not in cols:
        conn.execute("ALTER TABLE interfaces ADD COLUMN description TEXT NOT NULL DEFAULT ''")
    if "open_enabled" not in cols:
        conn.execute("ALTER TABLE interfaces ADD COLUMN open_enabled INTEGER NOT NULL DEFAULT 0")
        # 老数据兼容：已有 mcp_enabled=1 的接口，自动设为 open_enabled=1
        conn.execute("UPDATE interfaces SET open_enabled = 1 WHERE mcp_enabled = 1")


def get_setting(key: str, default=None):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
