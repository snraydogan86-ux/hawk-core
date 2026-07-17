"""
HAWK CORE — Memory.

Üç katman:
- Episodic Memory : yaşanan olaylar/aksiyonlar (zaman dizini)
- Semantic Memory : Knowledge Graph (özne-yüklem-nesne üçlüleri)
- Long-term Memory: kalıcı anahtar=değer bilgiler
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from core.pg_memory import _get_pool


# ── Episodic ────────────────────────────────────────────────────────────────
async def remember_episode(kind: str, content: str, *, meta: Optional[Dict] = None,
                           goal_id: Optional[int] = None, task_id: Optional[int] = None) -> None:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO hawk_core_episodic (kind, content, meta, goal_id, task_id)
               VALUES ($1,$2,$3::jsonb,$4,$5)""",
            kind, content[:4000], json.dumps(meta or {}), goal_id, task_id,
        )


async def recall_episodes(limit: int = 20, kind: Optional[str] = None) -> List[Dict[str, Any]]:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        if kind:
            rows = await conn.fetch(
                "SELECT kind, content, created_at FROM hawk_core_episodic WHERE kind=$1 ORDER BY id DESC LIMIT $2",
                kind, limit)
        else:
            rows = await conn.fetch(
                "SELECT kind, content, created_at FROM hawk_core_episodic ORDER BY id DESC LIMIT $1", limit)
        return [dict(r) for r in rows]


# ── Semantic / Knowledge Graph ──────────────────────────────────────────────
async def add_knowledge(subject: str, predicate: str, obj: str, *,
                        confidence: float = 0.8, source: str = "") -> None:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO hawk_core_knowledge (subject, predicate, object, confidence, source)
               VALUES ($1,$2,$3,$4,$5)
               ON CONFLICT (subject, predicate, object)
               DO UPDATE SET confidence=EXCLUDED.confidence, source=EXCLUDED.source""",
            subject[:300], predicate[:200], obj[:1000], confidence, source[:200],
        )


async def query_knowledge(subject: str, limit: int = 20) -> List[Dict[str, Any]]:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT subject, predicate, object, confidence FROM hawk_core_knowledge
               WHERE subject ILIKE $1 OR object ILIKE $1 ORDER BY confidence DESC LIMIT $2""",
            f"%{subject}%", limit)
        return [dict(r) for r in rows]


# ── Long-term ───────────────────────────────────────────────────────────────
async def set_fact(key: str, value: str) -> None:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO hawk_core_longterm (key, value, updated_at) VALUES ($1,$2,now())
               ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=now()""",
            key[:300], value[:4000])


async def get_fact(key: str) -> Optional[str]:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT value FROM hawk_core_longterm WHERE key=$1", key)
        return row["value"] if row else None


async def recent_context(limit: int = 8) -> str:
    """Beyin promptuna enjekte için kısa bağlam (son olaylar)."""
    eps = await recall_episodes(limit=limit)
    if not eps:
        return ""
    return "\n".join(f"- [{e['kind']}] {e['content'][:160]}" for e in eps)
