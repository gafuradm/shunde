"""Минимальный слой доступа к SQLite (thread-local соединения, WAL)."""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from .config import get_settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    filename    TEXT NOT NULL,
    ext         TEXT,
    mime        TEXT,
    size        INTEGER,
    language    TEXT,
    script      TEXT,
    classical   REAL DEFAULT 0,
    text        TEXT,
    char_count  INTEGER DEFAULT 0,
    status      TEXT DEFAULT 'uploaded',
    error       TEXT,
    meta        TEXT DEFAULT '{}',
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS annotations (
    id          TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    start_char  INTEGER NOT NULL,
    end_char    INTEGER NOT NULL,
    quote       TEXT NOT NULL,
    kind        TEXT NOT NULL,
    title       TEXT,
    rationale   TEXT,
    difficulty  INTEGER DEFAULT 3,
    created_by  TEXT DEFAULT 'ai',
    meta        TEXT DEFAULT '{}',
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_annotations_doc ON annotations(document_id, start_char);

CREATE TABLE IF NOT EXISTS explanations (
    id          TEXT PRIMARY KEY,
    cache_key   TEXT UNIQUE,
    document_id TEXT,
    quote       TEXT,
    mode        TEXT,
    ui_lang     TEXT,
    level       TEXT,
    answer      TEXT,
    sources     TEXT DEFAULT '[]',
    model       TEXT,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS lectures (
    id          TEXT PRIMARY KEY,
    code        TEXT UNIQUE NOT NULL,
    title       TEXT NOT NULL,
    document_id TEXT,
    target_lang TEXT DEFAULT 'en',
    source_lang TEXT DEFAULT 'zh-CN',
    status      TEXT DEFAULT 'idle',
    created_at  TEXT NOT NULL,
    started_at  TEXT,
    ended_at    TEXT
);

CREATE TABLE IF NOT EXISTS lecture_lines (
    id          TEXT PRIMARY KEY,
    lecture_id  TEXT NOT NULL,
    seq         INTEGER NOT NULL,
    source_text TEXT NOT NULL,
    source_lang TEXT,
    translated  TEXT,
    target_lang TEXT,
    is_final    INTEGER DEFAULT 1,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_lines_lecture ON lecture_lines(lecture_id, seq);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:12]}"


class Database:
    def __init__(self, path: Path) -> None:
        self.path = str(path)
        self._local = threading.local()

    # ---------- соединение ----------
    @property
    def conn(self) -> sqlite3.Connection:
        c: sqlite3.Connection | None = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self.path, check_same_thread=False, timeout=30.0)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")
            self._local.conn = c
        return c

    def init(self) -> None:
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # ---------- операции ----------
    def execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        cur = self.conn.execute(sql, params)
        self.conn.commit()
        return cur

    def executemany(self, sql: str, rows: Iterable[Sequence[Any]]) -> None:
        self.conn.executemany(sql, list(rows))
        self.conn.commit()

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[dict]:
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def one(self, sql: str, params: Sequence[Any] = ()) -> dict | None:
        row = self.conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    def insert(self, table: str, data: dict) -> None:
        cols = ", ".join(data.keys())
        marks = ", ".join("?" for _ in data)
        self.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", list(data.values()))

    def update(self, table: str, row_id: str, data: dict) -> None:
        if not data:
            return
        sets = ", ".join(f"{k} = ?" for k in data)
        self.execute(f"UPDATE {table} SET {sets} WHERE id = ?", [*data.values(), row_id])

    # ---------- json-хелперы ----------
    @staticmethod
    def loads(value: Any, default: Any = None) -> Any:
        if value in (None, ""):
            return default
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def dumps(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False)


db = Database(get_settings().db_path)
