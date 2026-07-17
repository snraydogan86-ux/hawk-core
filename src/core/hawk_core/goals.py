"""HAWK CORE — Goal Manager + Task Queue (kalıcı, Postgres)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.pg_memory import _get_pool


# ── Goals ───────────────────────────────────────────────────────────────────
async def create_goal(title: str, description: str = "", priority: int = 5) -> int:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO hawk_core_goals (title, description, priority)
               VALUES ($1,$2,$3) RETURNING id""",
            title[:500], description[:4000], priority)
        return int(row["id"])


async def update_goal(goal_id: int, *, status: Optional[str] = None,
                      progress: Optional[int] = None) -> None:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        if status is not None:
            await conn.execute("UPDATE hawk_core_goals SET status=$1, updated_at=now() WHERE id=$2",
                               status, goal_id)
        if progress is not None:
            await conn.execute("UPDATE hawk_core_goals SET progress=$1, updated_at=now() WHERE id=$2",
                               max(0, min(100, progress)), goal_id)


async def active_goals(limit: int = 20) -> List[Dict[str, Any]]:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, title, description, status, priority, progress FROM hawk_core_goals
               WHERE status='active' ORDER BY priority ASC, id ASC LIMIT $1""", limit)
        return [dict(r) for r in rows]


async def list_goals(limit: int = 50) -> List[Dict[str, Any]]:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, title, status, priority, progress FROM hawk_core_goals ORDER BY id DESC LIMIT $1", limit)
        return [dict(r) for r in rows]


# ── Task Queue ──────────────────────────────────────────────────────────────
async def add_task(title: str, detail: str = "", *, goal_id: Optional[int] = None,
                   tool: str = "", priority: int = 5) -> int:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO hawk_core_tasks (goal_id, title, detail, tool, priority)
               VALUES ($1,$2,$3,$4,$5) RETURNING id""",
            goal_id, title[:500], detail[:4000], tool[:100], priority)
        return int(row["id"])


async def next_pending(goal_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        if goal_id is not None:
            row = await conn.fetchrow(
                """SELECT * FROM hawk_core_tasks WHERE status='pending' AND goal_id=$1
                   ORDER BY priority ASC, id ASC LIMIT 1""", goal_id)
        else:
            row = await conn.fetchrow(
                """SELECT * FROM hawk_core_tasks WHERE status='pending'
                   ORDER BY priority ASC, id ASC LIMIT 1""")
        return dict(row) if row else None


async def update_task(task_id: int, *, status: Optional[str] = None,
                      result: Optional[str] = None, tool: Optional[str] = None,
                      inc_attempts: bool = False) -> None:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        sets, vals, i = [], [], 1
        if status is not None:
            sets.append(f"status=${i}"); vals.append(status); i += 1
        if result is not None:
            sets.append(f"result=${i}"); vals.append(result[:8000]); i += 1
        if tool is not None:
            sets.append(f"tool=${i}"); vals.append(tool[:100]); i += 1
        if inc_attempts:
            sets.append("attempts=attempts+1")
        sets.append("updated_at=now()")
        vals.append(task_id)
        await conn.execute(f"UPDATE hawk_core_tasks SET {', '.join(sets)} WHERE id=${i}", *vals)


async def list_tasks(goal_id: Optional[int] = None, limit: int = 50) -> List[Dict[str, Any]]:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        if goal_id is not None:
            rows = await conn.fetch(
                "SELECT id, title, status, tool, result FROM hawk_core_tasks WHERE goal_id=$1 ORDER BY id LIMIT $2",
                goal_id, limit)
        else:
            rows = await conn.fetch(
                "SELECT id, title, status, tool FROM hawk_core_tasks ORDER BY id DESC LIMIT $1", limit)
        return [dict(r) for r in rows]


async def pending_count() -> int:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        return int(await conn.fetchval("SELECT count(*) FROM hawk_core_tasks WHERE status='pending'") or 0)
