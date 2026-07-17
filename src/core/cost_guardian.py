"""
HAWK Cost Guardian — aynı işi tekrar tekrar pahalı modelle yapmayı önler.

Özellikler:
  * Response Cache (chat/think/ceo/tool) — TTL bazlı, duplicate detection.
  * Duplicate Detection — normalize + sha256 hash (embedding gerekmez).
  * Tool Result Cache — github/gpu_cloud/billing/token sinyalleri 5-15 dk cache.
  * CEO Brief / think cache — son üretim hâlâ geçerliyse 72B çağırma.
  * Cost Decision — cache_hit | small | medium | 72B | blocked | approval.

Salt-okuma/karar + cache; kritik işlem yürütmez. Sır yazmaz.
"""
from __future__ import annotations

import re
import json
import hashlib
from typing import Any, Dict, Optional

# Scope başına varsayılan TTL (saniye)
TTL = {
    "chat": 300,        # 5 dk
    "think": 1800,      # 30 dk
    "ceo_brief": 3600,  # 1 saat
    "tool": 600,        # 10 dk (5-15 dk aralığı)
}


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def req_hash(text: str, scope: str = "", user_id: str = "") -> str:
    raw = f"{scope}|{user_id}|{normalize(text)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


async def _pool():
    from db import get_pool
    return await get_pool()


async def cache_get(scope: str, h: str, ttl: Optional[int] = None) -> Optional[Dict[str, Any]]:
    ttl = ttl if ttl is not None else TTL.get(scope.split(":")[0], 300)
    try:
        pool = await _pool()
        async with pool.acquire() as con:
            row = await con.fetchrow(
                """SELECT response, created_at,
                          EXTRACT(EPOCH FROM (now() - created_at)) AS age
                   FROM public.response_cache
                   WHERE scope=$1 AND request_hash=$2""", scope, h)
            if not row:
                return None
            if float(row["age"]) > ttl:
                return None
            resp = row["response"]
            if isinstance(resp, str):
                resp = json.loads(resp)
            return {"cached": True, "age_seconds": int(row["age"]),
                    "created_at": str(row["created_at"]), "response": resp}
    except Exception:
        return None


async def cache_put(scope: str, h: str, response: Any, tier: str = "", ttl: Optional[int] = None) -> bool:
    ttl = ttl if ttl is not None else TTL.get(scope.split(":")[0], 300)
    try:
        pool = await _pool()
        async with pool.acquire() as con:
            await con.execute(
                """INSERT INTO public.response_cache (request_hash, scope, response, tier, expires_at)
                   VALUES ($1,$2,$3::jsonb,$4, now() + ($5 || ' seconds')::interval)
                   ON CONFLICT (scope, request_hash)
                   DO UPDATE SET response=EXCLUDED.response, tier=EXCLUDED.tier,
                                 created_at=now(), expires_at=EXCLUDED.expires_at""",
                h, scope, json.dumps(response, default=str), tier, str(ttl))
        return True
    except Exception:
        return False


async def tool_cache_get(name: str, ttl: int = 600) -> Optional[Dict[str, Any]]:
    return await cache_get(f"tool:{name}", req_hash(name, scope="tool"), ttl=ttl)


async def tool_cache_put(name: str, result: Any, ttl: int = 600) -> bool:
    return await cache_put(f"tool:{name}", req_hash(name, scope="tool"), result, tier="tier1", ttl=ttl)


async def cost_decision(text: str, *, scope: str = "chat", user_plan: str = "free",
                        tool_count: int = 0, reasoning_required: bool = False,
                        critical: bool = False) -> Dict[str, Any]:
    """Tek noktadan karar: cache_hit | small | medium | 72B | blocked | approval."""
    from core import model_router, economy_manager

    h = req_hash(text, scope=scope)
    cached = await cache_get(scope, h)
    if cached:
        return {"decision": "cache_hit", "tier": "cache", "request_hash": h,
                "cache": {"age_seconds": cached["age_seconds"]},
                "reason": "Cache geçerli — pahalı model çağrılmadı."}

    routed = await model_router.decide_tier(
        text, user_plan=user_plan, tool_count=tool_count,
        reasoning_required=reasoning_required, critical=critical)
    tier = routed["allowed_tier"]
    label = {"tier1": "small", "tier2": "medium", "tier3": "72B"}.get(tier, "medium")
    return {"decision": label, "tier": tier, "request_hash": h,
            "complexity_score": routed.get("complexity_score"),
            "reason": routed.get("reason"), "budget_level": routed.get("budget", {}).get("level")}


async def stats() -> Dict[str, Any]:
    from core import economy_manager
    s = await economy_manager.stats_24h()
    budget = await economy_manager.budget_status()
    try:
        pool = await _pool()
        async with pool.acquire() as con:
            cache_rows = await con.fetchval("SELECT count(*) FROM public.response_cache")
            tier3_avoided = await con.fetchval(
                """SELECT count(*) FROM public.model_call_logs
                   WHERE cache_hit AND tier='tier3' AND created_at >= now() - interval '1 day'""")
            saved = await con.fetchval(
                """SELECT COALESCE(SUM(saved_cost),0) FROM public.model_call_logs
                   WHERE created_at >= now() - interval '1 day'""")
    except Exception:
        cache_rows, tier3_avoided, saved = 0, 0, 0
    return {"cache_hit_rate": s["cache_hit_rate"], "cache_entries": int(cache_rows or 0),
            "estimated_saved_cost_24h": round(float(saved or 0), 6),
            "requests_24h": s["calls_24h"], "tier3_avoided_24h": int(tier3_avoided or 0),
            "budget": budget}
