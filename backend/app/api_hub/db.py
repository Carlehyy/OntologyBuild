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
    body_type     TEXT    NOT NULL DEFAULT 'none', -- none|json|form|multipart|raw
    body_content  TEXT    NOT NULL DEFAULT '',
    file_fields   TEXT    NOT NULL DEFAULT '[]',   -- JSON: multipart file field definitions
    use_w3        INTEGER NOT NULL DEFAULT 0,
    mcp_enabled   INTEGER NOT NULL DEFAULT 0,
    open_enabled  INTEGER NOT NULL DEFAULT 0,
    http_enabled  INTEGER NOT NULL DEFAULT 0,
    proxy_slug    TEXT    NOT NULL DEFAULT '',
    proxy_query_keys  TEXT NOT NULL DEFAULT '[]',
    proxy_header_keys TEXT NOT NULL DEFAULT '[]',
    proxy_body_enabled INTEGER NOT NULL DEFAULT 0,
    proxy_body_keys TEXT NOT NULL DEFAULT '[]',
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
    source            TEXT    NOT NULL DEFAULT 'ui',
    proxy_key_id      INTEGER,
    proxy_key_name    TEXT,
    source_ip         TEXT,
    created_at       TEXT    NOT NULL,
    FOREIGN KEY (interface_id) REFERENCES interfaces(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS proxy_keys (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT    NOT NULL,
    key_prefix    TEXT    NOT NULL,
    key_hash      TEXT    NOT NULL UNIQUE,
    enabled       INTEGER NOT NULL DEFAULT 1,
    valid_from    TEXT,
    expires_at    TEXT,
    scope_all     INTEGER NOT NULL DEFAULT 0,
    last_used_at  TEXT,
    created_at    TEXT    NOT NULL,
    updated_at    TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS proxy_key_interfaces (
    key_id        INTEGER NOT NULL,
    interface_id  INTEGER NOT NULL,
    PRIMARY KEY (key_id, interface_id),
    FOREIGN KEY (key_id) REFERENCES proxy_keys(id) ON DELETE CASCADE,
    FOREIGN KEY (interface_id) REFERENCES interfaces(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS credential_usage (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    interface_id   INTEGER,
    interface_name TEXT    NOT NULL DEFAULT '',
    ok             INTEGER NOT NULL DEFAULT 0,
    relogin        INTEGER NOT NULL DEFAULT 0,
    status_code    INTEGER,
    error          TEXT,
    created_at     TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_credential_usage_created
ON credential_usage(created_at DESC);
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
    if "http_enabled" not in cols:
        conn.execute("ALTER TABLE interfaces ADD COLUMN http_enabled INTEGER NOT NULL DEFAULT 0")
    if "proxy_slug" not in cols:
        conn.execute("ALTER TABLE interfaces ADD COLUMN proxy_slug TEXT NOT NULL DEFAULT ''")
    if "proxy_query_keys" not in cols:
        conn.execute("ALTER TABLE interfaces ADD COLUMN proxy_query_keys TEXT NOT NULL DEFAULT '[]'")
    if "proxy_header_keys" not in cols:
        conn.execute("ALTER TABLE interfaces ADD COLUMN proxy_header_keys TEXT NOT NULL DEFAULT '[]'")
    if "proxy_body_enabled" not in cols:
        conn.execute(
            "ALTER TABLE interfaces ADD COLUMN proxy_body_enabled INTEGER NOT NULL DEFAULT 0"
        )
    if "proxy_body_keys" not in cols:
        conn.execute(
            "ALTER TABLE interfaces ADD COLUMN proxy_body_keys TEXT NOT NULL DEFAULT '[]'"
        )
    if "file_fields" not in cols:
        conn.execute(
            "ALTER TABLE interfaces ADD COLUMN file_fields TEXT NOT NULL DEFAULT '[]'"
        )

    run_cols = {r["name"] for r in conn.execute("PRAGMA table_info(runs)").fetchall()}
    if "source" not in run_cols:
        conn.execute("ALTER TABLE runs ADD COLUMN source TEXT NOT NULL DEFAULT 'ui'")
    if "proxy_key_id" not in run_cols:
        conn.execute("ALTER TABLE runs ADD COLUMN proxy_key_id INTEGER")
    if "proxy_key_name" not in run_cols:
        conn.execute("ALTER TABLE runs ADD COLUMN proxy_key_name TEXT")
    if "source_ip" not in run_cols:
        conn.execute("ALTER TABLE runs ADD COLUMN source_ip TEXT")

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS proxy_keys (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            name          TEXT    NOT NULL,
            key_prefix    TEXT    NOT NULL,
            key_hash      TEXT    NOT NULL UNIQUE,
            enabled       INTEGER NOT NULL DEFAULT 1,
            valid_from    TEXT,
            expires_at    TEXT,
            scope_all     INTEGER NOT NULL DEFAULT 0,
            last_used_at  TEXT,
            created_at    TEXT    NOT NULL,
            updated_at    TEXT    NOT NULL
        );
        CREATE TABLE IF NOT EXISTS proxy_key_interfaces (
            key_id        INTEGER NOT NULL,
            interface_id  INTEGER NOT NULL,
            PRIMARY KEY (key_id, interface_id),
            FOREIGN KEY (key_id) REFERENCES proxy_keys(id) ON DELETE CASCADE,
            FOREIGN KEY (interface_id) REFERENCES interfaces(id) ON DELETE CASCADE
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_interfaces_active_proxy_slug
        ON interfaces(proxy_slug)
        WHERE http_enabled = 1 AND proxy_slug <> '';
        CREATE INDEX IF NOT EXISTS idx_proxy_keys_hash ON proxy_keys(key_hash);
        CREATE INDEX IF NOT EXISTS idx_runs_proxy_key_id ON runs(proxy_key_id);
        """
    )


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


def record_credential_usage(interface: dict, result: dict, created_at: str) -> None:
    """Record one W3-assisted interface attempt without retaining credentials."""
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO credential_usage(interface_id, interface_name, ok, relogin, "
            "status_code, error, created_at) VALUES(?,?,?,?,?,?,?)",
            (
                interface.get("id"),
                interface.get("name") or "未命名接口",
                1 if result.get("ok") else 0,
                1 if result.get("relogin") else 0,
                result.get("status_code"),
                result.get("error"),
                created_at,
            ),
        )


def credential_usage_stats(limit: int = 60) -> dict:
    limit = max(1, min(limit, 200))
    with get_conn() as conn:
        totals = conn.execute(
            "SELECT COUNT(*) AS total, "
            "SUM(CASE WHEN ok = 1 THEN 1 ELSE 0 END) AS success, "
            "SUM(CASE WHEN ok = 0 THEN 1 ELSE 0 END) AS failed, "
            "SUM(CASE WHEN relogin = 1 THEN 1 ELSE 0 END) AS relogin "
            "FROM credential_usage"
        ).fetchone()
        rows = conn.execute(
            "SELECT id, interface_id, interface_name, ok, relogin, status_code, "
            "error, created_at FROM credential_usage ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    total = int(totals["total"] or 0)
    success = int(totals["success"] or 0)
    failed = int(totals["failed"] or 0)
    return {
        "total": total,
        "success": success,
        "failed": failed,
        "relogin": int(totals["relogin"] or 0),
        "success_rate": round(success * 100 / total, 1) if total else 0,
        "recent": [
            {
                **dict(row),
                "ok": bool(row["ok"]),
                "relogin": bool(row["relogin"]),
            }
            for row in rows
        ],
    }
