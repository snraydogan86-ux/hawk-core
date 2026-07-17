from __future__ import annotations
import os
import sqlite3
from contextlib import contextmanager
from typing import List, Dict, Any

DB_URL = os.getenv("DATABASE_URL", "").strip()
SQLITE_FALLBACK = "/tmp/hawk_session_memory.sqlite3"

def _use_pg() -> bool:
    return False

@contextmanager
def get_conn():
    if _use_pg():
        import psycopg2
        conn = psycopg2.connect(DB_URL)
        conn.autocommit = True
        try:
            yield conn
        finally:
            conn.close()
    else:
        conn = sqlite3.connect(SQLITE_FALLBACK)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

def init_conversation_db() -> None:
    with get_conn() as conn:
        cur = conn.cursor()
        if _use_pg():
            cur.execute("""
            CREATE TABLE IF NOT EXISTS hawk_sessions (
                session_id TEXT PRIMARY KEY,
                title TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
            """)
            cur.execute("""
            CREATE TABLE IF NOT EXISTS hawk_messages (
                id BIGSERIAL PRIMARY KEY,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_hawk_messages_session_id ON hawk_messages(session_id)")
        else:
            cur.execute("""
            CREATE TABLE IF NOT EXISTS hawk_sessions (
                session_id TEXT PRIMARY KEY,
                title TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """)
            cur.execute("""
            CREATE TABLE IF NOT EXISTS hawk_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_hawk_messages_session_id ON hawk_messages(session_id)")

def ensure_session(session_id: str, title: str | None = None) -> None:
    session_id = str(session_id or "").strip()
    if not session_id:
        return

    with get_conn() as conn:
        cur = conn.cursor()
        if _use_pg():
            cur.execute(
                """
                INSERT INTO hawk_sessions(session_id, title)
                VALUES (%s, %s)
                ON CONFLICT (session_id) DO NOTHING
                """,
                (session_id, title or "Yeni sohbet"),
            )
            cur.execute(
                "UPDATE hawk_sessions SET updated_at = NOW() WHERE session_id = %s",
                (session_id,),
            )
        else:
            cur.execute(
                "INSERT OR IGNORE INTO hawk_sessions(session_id, title) VALUES (?, ?)",
                (session_id, title or "Yeni sohbet"),
            )
            cur.execute(
                "UPDATE hawk_sessions SET updated_at = CURRENT_TIMESTAMP WHERE session_id = ?",
                (session_id,),
            )

def maybe_set_title(session_id: str, first_user_message: str) -> None:
    title = (first_user_message or "").strip()
    if not title:
        return
    title = title[:60]

    with get_conn() as conn:
        cur = conn.cursor()
        if _use_pg():
            cur.execute(
                """
                UPDATE hawk_sessions
                SET title = CASE
                    WHEN title IS NULL OR title = '' OR title = 'Yeni sohbet' THEN %s
                    ELSE title
                END,
                updated_at = NOW()
                WHERE session_id = %s
                """,
                (title, session_id),
            )
        else:
            cur.execute(
                """
                UPDATE hawk_sessions
                SET title = CASE
                    WHEN title IS NULL OR title = '' OR title = 'Yeni sohbet' THEN ?
                    ELSE title
                END,
                updated_at = CURRENT_TIMESTAMP
                WHERE session_id = ?
                """,
                (title, session_id),
            )

def add_message(session_id: str, role: str, content: str) -> None:
    if not session_id or not role or not str(content or "").strip():
        return

    with get_conn() as conn:
        cur = conn.cursor()
        if _use_pg():
            cur.execute(
                "INSERT INTO hawk_messages(session_id, role, content) VALUES (%s, %s, %s)",
                (session_id, role, content),
            )
            cur.execute(
                "UPDATE hawk_sessions SET updated_at = NOW() WHERE session_id = %s",
                (session_id,),
            )
        else:
            cur.execute(
                "INSERT INTO hawk_messages(session_id, role, content) VALUES (?, ?, ?)",
                (session_id, role, content),
            )
            cur.execute(
                "UPDATE hawk_sessions SET updated_at = CURRENT_TIMESTAMP WHERE session_id = ?",
                (session_id,),
            )

def get_recent_messages(session_id: str, limit: int = 10) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        cur = conn.cursor()
        if _use_pg():
            cur.execute(
                """
                SELECT role, content, created_at
                FROM hawk_messages
                WHERE session_id = %s
                ORDER BY id DESC
                LIMIT %s
                """,
                (session_id, limit),
            )
            rows = cur.fetchall()
            rows = list(reversed(rows))
            return [
                {"role": r[0], "content": r[1], "created_at": str(r[2])}
                for r in rows
            ]
        else:
            cur.execute(
                """
                SELECT role, content, created_at
                FROM hawk_messages
                WHERE session_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (session_id, limit),
            )
            rows = cur.fetchall()
            rows = list(reversed(rows))
            return [
                {"role": r["role"], "content": r["content"], "created_at": r["created_at"]}
                for r in rows
            ]

def list_sessions(limit: int = 50) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        cur = conn.cursor()
        if _use_pg():
            cur.execute(
                """
                SELECT session_id, COALESCE(title, 'Yeni sohbet') AS title, updated_at
                FROM hawk_sessions
                ORDER BY updated_at DESC
                LIMIT %s
                """,
                (limit,),
            )
            rows = cur.fetchall()
            return [
                {"id": r[0], "title": r[1], "updated_at": str(r[2])}
                for r in rows
            ]
        else:
            cur.execute(
                """
                SELECT session_id, COALESCE(title, 'Yeni sohbet') AS title, updated_at
                FROM hawk_sessions
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (limit,),
            )
            rows = cur.fetchall()
            return [
                {"id": r["session_id"], "title": r["title"], "updated_at": r["updated_at"]}
                for r in rows
            ]

def get_session_messages(session_id: str, limit: int = 200) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        cur = conn.cursor()
        if _use_pg():
            cur.execute(
                """
                SELECT role, content, created_at
                FROM hawk_messages
                WHERE session_id = %s
                ORDER BY id ASC
                LIMIT %s
                """,
                (session_id, limit),
            )
            rows = cur.fetchall()
            return [
                {"id": f"{session_id}-{i}", "role": r[0], "content": r[1], "created_at": str(r[2])}
                for i, r in enumerate(rows, start=1)
            ]
        else:
            cur.execute(
                """
                SELECT id, role, content, created_at
                FROM hawk_messages
                WHERE session_id = ?
                ORDER BY id ASC
                LIMIT ?
                """,
                (session_id, limit),
            )
            rows = cur.fetchall()
            return [
                {"id": f"{session_id}-{r['id']}", "role": r["role"], "content": r["content"], "created_at": r["created_at"]}
                for r in rows
            ]
