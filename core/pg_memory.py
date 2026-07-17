"""
HAWK PostgreSQL Memory Engine — production-grade bellek sistemi.

SQLite memory_store.py'nin ölçeklenebilir halefidir.
hawk_user_memory_facts + hawk_user_memory_history tablolarını kullanır.
Tamamen async, connection pool destekli.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import asyncpg

_DATABASE_URL = os.getenv("DATABASE_URL", f"postgresql://hawk:{os.getenv('POSTGRES_PASSWORD','')}@hawk-v2-db:5432/hawkdb")
_pool: asyncpg.Pool | None = None


async def _get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None or _pool._closed:
        _pool = await asyncpg.create_pool(
            _DATABASE_URL,
            min_size=1,
            max_size=5,
            command_timeout=15,
            server_settings={"application_name": "hawk-memory"},
        )
    return _pool


# ─────────────────────── FACTS ─────────────────────────

async def pg_remember(user_id: str, key: str, value: str) -> None:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO hawk_user_memory_facts(user_id, key, value, updated_at)
            VALUES($1, $2, $3, NOW())
            ON CONFLICT(user_id, key) DO UPDATE
              SET value=$3, updated_at=NOW()
            """,
            user_id, key, value,
        )
    # NOT: cloud database yazımı artık pg_remember'da DEĞİL — merkezi memory_pipeline
    # üzerinden yapılır (tek yazma yolu). Burası yalnız Postgres katmanıdır.


async def pg_recall(user_id: str, key: str) -> Optional[str]:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT value FROM hawk_user_memory_facts WHERE user_id=$1 AND key=$2",
            user_id, key,
        )
    return row["value"] if row else None


async def pg_all_facts(user_id: str) -> Dict[str, str]:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT key, value FROM hawk_user_memory_facts WHERE user_id=$1 ORDER BY updated_at DESC LIMIT 50",
            user_id,
        )
    return {r["key"]: r["value"] for r in rows}


async def pg_forget(user_id: str, key: str) -> None:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM hawk_user_memory_facts WHERE user_id=$1 AND key=$2",
            user_id, key,
        )


# ─────────────────────── HISTORY ───────────────────────

async def pg_add_history(user_id: str, role: str, content: str) -> None:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO hawk_user_memory_history(user_id, role, content) VALUES($1,$2,$3)",
            user_id, role, content[:2000],
        )


async def pg_get_history(user_id: str, limit: int = 20) -> List[Dict[str, str]]:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT role, content, created_at
            FROM hawk_user_memory_history
            WHERE user_id=$1
            ORDER BY id DESC
            LIMIT $2
            """,
            user_id, limit,
        )
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


async def pg_clear_history(user_id: str) -> None:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM hawk_user_memory_history WHERE user_id=$1",
            user_id,
        )


# ─────────────────────── SNAPSHOT ──────────────────────

async def pg_snapshot(user_id: str) -> Dict[str, Any]:
    """Tüm bellek — facts + son 20 konuşma."""
    facts = await pg_all_facts(user_id)
    history = await pg_get_history(user_id, limit=20)
    return {
        "user_id": user_id,
        "facts": facts,
        "history": history,
    }


async def pg_search_facts(user_id: str, query: str) -> Dict[str, str]:
    """Fact'ler içinde anahtar kelimeyle ara (key veya value ILIKE)."""
    pool = await _get_pool()
    q = f"%{query}%"
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT key, value FROM hawk_user_memory_facts
            WHERE user_id=$1 AND (key ILIKE $2 OR value ILIKE $2)
            ORDER BY updated_at DESC LIMIT 20
            """,
            user_id, q,
        )
    return {r["key"]: r["value"] for r in rows}


async def pg_prune_history(user_id: str, keep: int = 200) -> int:
    """Son `keep` mesaj dışındaki geçmişi sil — depolama yönetimi."""
    pool = await _get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            DELETE FROM hawk_user_memory_history
            WHERE user_id=$1 AND id NOT IN (
                SELECT id FROM hawk_user_memory_history
                WHERE user_id=$1
                ORDER BY id DESC
                LIMIT $2
            )
            """,
            user_id, keep,
        )
    deleted = int(result.split()[-1]) if result else 0
    return deleted


# ─────────────────────── EXTRACT & STORE ───────────────

import re as _re

# Soru kalıpları — bunlar asla fact olarak kaydedilmemeli
_QUERY_PATTERNS = _re.compile(
    r"(?:adım ne|ismim ne|adın ne|kim olduğumu|ne diyorsun|biliyor musun|hatırlıyor musun"
    r"|what is my name|what's my name|do you remember|who am I)",
    _re.I
)

_FACT_PATTERNS = [
    # TR: "benim adım Kemal" / "ismim Kemal"
    ("name", r"(?:benim adım|ismim)\s+([A-Za-zÇçĞğİıÖöŞşÜü]{3,30})"),
    # EN: "my name is Kemal" / "I'm Kemal" / "call me Kemal"
    ("name", r"(?:my name is|I am|I'm|call me)\s+([A-Za-z]{3,30})(?:\s|$|,|\.)"),
    # TR: "İstanbul'da yaşıyorum"
    ("city", r"([A-ZÇĞİÖŞÜ][a-zçğışöüA-ZÇĞİÖŞÜa-zçğışöü]{2,20})['''\s]?(?:da|de|ta|te)\s+(?:yaşıyorum|oturuyorum|ikamet ediyorum)"),
    # TR: "şehrim İstanbul"
    ("city", r"şehrim\s+([A-Za-zÇçĞğİıÖöŞşÜü]{3,20})"),
    # EN: "I live in Istanbul" / "I'm from Berlin" / "I'm based in London"
    ("city", r"(?:I live in|I'm from|I am from|I'm based in|based in)\s+([A-Z][a-z]{2,20})"),
    # TR: "yazılım mühendisiyim" / "doktorayım"
    ("job", r"([A-Za-zÇçĞğİıÖöŞşÜü\s]{3,30}?)\s*(?:mühendisiyim|doktoruyum|avukatım|öğretmenim|öğrenciyim)"),
    # TR: "mesleğim yazılım mühendisi"
    ("job", r"(?:mesleğim|işim)\s+([A-Za-zÇçĞğİıÖöŞşÜü ]{3,30})"),
    # EN: "I'm a software engineer" / "I work as a developer"
    ("job", r"(?:I'm a|I am a|I work as a?)\s+([A-Za-z ]{3,30})(?:\s|$|,|\.)"),
    # TR: "anadilim Türkçe"
    ("language", r"(?:anadilim|dilim)\s+([A-Za-zÇçĞğİıÖöŞşÜü]{3,20})"),
    # EN: "my native language is Turkish"
    ("language", r"(?:my native language is|I speak)\s+([A-Za-z]{3,20})"),
    # TR: "hobim müzik" / "ilgi alanım programlama"
    ("hobby", r"(?:hobim|ilgi alanım|sevdiğim şey)\s+([A-Za-zÇçĞğİıÖöŞşÜü ]{3,30})"),
    # EN: "my hobby is music" / "I love programming"
    ("hobby", r"(?:my hobby is|I love|I enjoy|I like)\s+([A-Za-z ]{3,30})(?:\s|$|,|\.)"),
]

_STOP_WORDS = {
    "ne", "bu", "şu", "ki", "da", "de", "mi", "mu", "mı",
    "a", "an", "the", "is", "are", "am", "be", "to", "do",
}

async def pg_extract_and_store(message: str, user_id: str) -> None:
    if _QUERY_PATTERNS.search(message):
        return
    for key, pattern in _FACT_PATTERNS:
        m = _re.search(pattern, message, _re.IGNORECASE)
        if m:
            value = m.group(1).strip().rstrip(".,!?")
            if value and len(value) >= 3 and value.lower() not in _STOP_WORDS:
                await pg_remember(user_id, key, value)
