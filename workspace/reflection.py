"""HAWK CORE — Self Reflection + Self Critique.

Her görev sonrası: sonucu eleştir, ders çıkar, puanla. Dersler hafızaya yazılır;
Self-Improvement döngüsü bunları kullanır.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from core.pg_memory import _get_pool

from .engine import brain_chat
from .planner import _extract_json


async def reflect(task_title: str, result: str, *, goal_id: Optional[int] = None,
                  task_id: Optional[int] = None) -> Dict[str, Any]:
    """Sonucu eleştir → {critique, lesson, score(0-10)} ; kaydeder."""
    prompt = (
        "Sen HAWK'ın Öz-Eleştiri Motorusun. Aşağıdaki görev sonucunu dürüstçe değerlendir. "
        'SADECE JSON döndür: {"critique": "ne iyi/kötü", "lesson": "bir sonraki için ders", '
        '"score": 0-10}.\n\n'
        f"GÖREV: {task_title}\nSONUÇ: {result[:2000]}\n\nJSON:"
    )
    r = await brain_chat([{"role": "user", "content": prompt}], temperature=0.2, max_tokens=300)
    data = _extract_json(r.get("text", "")) if r.get("ok") else None
    out = {"critique": "", "lesson": "", "score": 0}
    if isinstance(data, dict):
        out["critique"] = str(data.get("critique") or "")[:2000]
        out["lesson"] = str(data.get("lesson") or "")[:2000]
        try:
            out["score"] = max(0, min(10, int(data.get("score") or 0)))
        except Exception:
            out["score"] = 0
    try:
        pool = await _get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO hawk_core_reflections (task_id, goal_id, critique, lesson, score)
                   VALUES ($1,$2,$3,$4,$5)""",
                task_id, goal_id, out["critique"], out["lesson"], out["score"])
    except Exception:
        pass
    return out


async def recent_lessons(limit: int = 10) -> list[str]:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT lesson FROM hawk_core_reflections WHERE lesson<>'' ORDER BY id DESC LIMIT $1", limit)
        return [r["lesson"] for r in rows]
