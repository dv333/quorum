"""SQLite storage. A single connection guarded by a lock; all calls are short."""

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from .config import DB_PATH, DEFAULT_ENDPOINTS

SCHEMA = """
CREATE TABLE IF NOT EXISTS endpoints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    base_url TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS debates (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    chair_endpoint_id INTEGER NOT NULL,
    chair_model TEXT NOT NULL,
    max_rounds INTEGER NOT NULL,
    autopilot INTEGER NOT NULL DEFAULT 0,
    criteria_json TEXT NOT NULL DEFAULT '[]',
    custom_rubric TEXT NOT NULL DEFAULT '',
    num_ctx INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'idle',
    topic INTEGER NOT NULL DEFAULT 0,
    round INTEGER NOT NULL DEFAULT 0,
    research_enabled INTEGER NOT NULL DEFAULT 0,
    researcher_endpoint_id INTEGER,
    researcher_model TEXT,
    chair_mode TEXT NOT NULL DEFAULT 'manual',
    chair_handle TEXT,
    chair_reason TEXT,
    chair_picked_by TEXT,
    researcher_handle TEXT
);
CREATE TABLE IF NOT EXISTS seats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    debate_id TEXT NOT NULL REFERENCES debates(id) ON DELETE CASCADE,
    handle TEXT NOT NULL,
    endpoint_id INTEGER NOT NULL,
    model TEXT NOT NULL,
    color TEXT NOT NULL,
    thinking_enabled INTEGER NOT NULL DEFAULT 1,
    position INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    debate_id TEXT NOT NULL REFERENCES debates(id) ON DELETE CASCADE,
    topic INTEGER NOT NULL,
    round INTEGER NOT NULL,
    author_kind TEXT NOT NULL,
    seat_id INTEGER,
    content TEXT NOT NULL DEFAULT '',
    thinking TEXT NOT NULL DEFAULT '',
    stance TEXT,
    position_line TEXT,
    stance_parsed INTEGER NOT NULL DEFAULT 0,
    tokens INTEGER,
    tok_per_s REAL,
    status TEXT NOT NULL DEFAULT 'done',
    research_kind TEXT,
    research_request TEXT,
    requested_by TEXT,
    sources_json TEXT NOT NULL DEFAULT '[]',
    prompt_tokens INTEGER,
    duration_ms INTEGER,
    meta_json TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    debate_id TEXT NOT NULL REFERENCES debates(id) ON DELETE CASCADE,
    topic INTEGER NOT NULL,
    actor TEXT NOT NULL,
    model TEXT NOT NULL,
    kind TEXT NOT NULL,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    searches INTEGER NOT NULL DEFAULT 0,
    pages INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS verdict_levels (
    verdict_id INTEGER NOT NULL REFERENCES verdicts(id) ON DELETE CASCADE,
    level TEXT NOT NULL,
    content TEXT NOT NULL,
    PRIMARY KEY (verdict_id, level)
);
CREATE TABLE IF NOT EXISTS summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    debate_id TEXT NOT NULL REFERENCES debates(id) ON DELETE CASCADE,
    topic INTEGER NOT NULL,
    upto_round INTEGER NOT NULL,
    content TEXT NOT NULL,
    source_messages INTEGER NOT NULL DEFAULT 0,
    source_tokens INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS verdicts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    debate_id TEXT NOT NULL REFERENCES debates(id) ON DELETE CASCADE,
    topic INTEGER NOT NULL,
    reason TEXT NOT NULL,
    rounds INTEGER NOT NULL,
    message_id INTEGER,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_debate ON messages(debate_id, id);
"""

# Columns added after v1; applied to existing databases on connect
MIGRATIONS = [
    ("debates", "research_enabled", "INTEGER NOT NULL DEFAULT 0"),
    ("debates", "researcher_endpoint_id", "INTEGER"),
    ("debates", "researcher_model", "TEXT"),
    ("messages", "research_kind", "TEXT"),
    ("messages", "research_request", "TEXT"),
    ("messages", "requested_by", "TEXT"),
    ("messages", "sources_json", "TEXT NOT NULL DEFAULT '[]'"),
    ("messages", "prompt_tokens", "INTEGER"),
    ("messages", "duration_ms", "INTEGER"),
    ("debates", "chair_mode", "TEXT NOT NULL DEFAULT 'manual'"),
    ("debates", "chair_handle", "TEXT"),
    ("debates", "chair_reason", "TEXT"),
    ("debates", "chair_picked_by", "TEXT"),
    ("debates", "researcher_handle", "TEXT"),
    ("endpoints", "api_key", "TEXT"),
    ("messages", "meta_json", "TEXT"),
]

_lock = threading.RLock()
_conn: Optional[sqlite3.Connection] = None


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect(path: str = DB_PATH) -> sqlite3.Connection:
    """Open (or reopen) the database and apply the schema."""
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
        if path != ":memory:":
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        _conn = sqlite3.connect(path, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA foreign_keys = ON")
        _conn.execute("PRAGMA journal_mode = WAL") if path != ":memory:" else None
        _conn.executescript(SCHEMA)
        for table, column, ddl in MIGRATIONS:
            cols = {r["name"] for r in _conn.execute(f"PRAGMA table_info({table})")}
            if column not in cols:
                _conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
        for name, url, kind in DEFAULT_ENDPOINTS:
            _conn.execute(
                "INSERT OR IGNORE INTO endpoints (name, base_url, kind) VALUES (?, ?, ?)",
                (name, url, kind),
            )
        _conn.commit()
        return _conn


def conn() -> sqlite3.Connection:
    return _conn if _conn is not None else connect()


def query(sql: str, params: Iterable[Any] = ()) -> List[Dict[str, Any]]:
    with _lock:
        return [dict(r) for r in conn().execute(sql, tuple(params)).fetchall()]


def query_one(sql: str, params: Iterable[Any] = ()) -> Optional[Dict[str, Any]]:
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params: Iterable[Any] = ()) -> int:
    """Execute a write and return lastrowid."""
    with _lock:
        cur = conn().execute(sql, tuple(params))
        conn().commit()
        return cur.lastrowid


def update(table: str, row_id: Any, **fields: Any) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    execute(f"UPDATE {table} SET {cols} WHERE id = ?", [*fields.values(), row_id])


def get_setting(key: str, default: Optional[str] = None) -> Optional[str]:
    row = query_one("SELECT value FROM settings WHERE key = ?", [key])
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        [key, value],
    )


def loads(value: Optional[str], default: Any = None) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default
