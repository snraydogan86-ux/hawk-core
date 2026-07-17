"""
HAWK Hibrit GPU — canlı yerel GPU durumu (heartbeat) + canlı yönlendirme.

Yerel GPU agent periyodik heartbeat gönderir (available/vram/load). HAWK en taze
heartbeat'i tutar. gpu_router kararı bu CANLI duruma göre verilir:
  - Taze heartbeat + available → YEREL kullanılabilir.
  - Heartbeat bayat (PC kapalı / agent durdu) → GPU OFFLINE → online/api fallback.

freshness (tazelik) = last_seen son `fresh_sec` içinde mi?
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from core.pg_memory import _get_pool
from core import gpu_router

FRESH_SEC_DEFAULT = 60  # bu süre içinde heartbeat yoksa GPU offline sayılır


async def record_heartbeat(host: str, *, owner_email: Optional[str] = None,
                           available: bool = True, vram_gb: float = 0.0,
                           load: float = 0.0, gpu_name: Optional[str] = None,
                           meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Yerel agent'tan heartbeat kaydet (upsert, last_seen=now())."""
    import json
    pool = await _get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO hawk_gpu_heartbeats
               (host, owner_email, available, vram_gb, load_pct, gpu_name, meta, last_seen)
               VALUES ($1,$2,$3,$4,$5,$6,$7, now())
               ON CONFLICT (host) DO UPDATE SET
                 owner_email=EXCLUDED.owner_email, available=EXCLUDED.available,
                 vram_gb=EXCLUDED.vram_gb, load_pct=EXCLUDED.load_pct,
                 gpu_name=EXCLUDED.gpu_name, meta=EXCLUDED.meta, last_seen=now()""",
            host[:120], owner_email, bool(available), float(vram_gb or 0),
            float(load or 0), (gpu_name or "")[:120], json.dumps(meta or {}, ensure_ascii=False))
    return {"ok": True, "host": host}


async def local_gpu_status(*, owner_email: Optional[str] = None,
                           fresh_sec: int = FRESH_SEC_DEFAULT) -> Dict[str, Any]:
    """
    En taze yerel GPU durumu → gpu_router.route_compute'un beklediği biçim:
    {available, vram_gb, load, host, fresh, age_sec}. Taze heartbeat yoksa available=False.
    """
    pool = await _get_pool()
    async with pool.acquire() as conn:
        if owner_email:
            row = await conn.fetchrow(
                """SELECT host, available, vram_gb, load_pct, gpu_name,
                          EXTRACT(EPOCH FROM (now()-last_seen)) AS age
                   FROM hawk_gpu_heartbeats WHERE owner_email=$1
                   ORDER BY last_seen DESC LIMIT 1""", owner_email)
        else:
            row = await conn.fetchrow(
                """SELECT host, available, vram_gb, load_pct, gpu_name,
                          EXTRACT(EPOCH FROM (now()-last_seen)) AS age
                   FROM hawk_gpu_heartbeats ORDER BY last_seen DESC LIMIT 1""")
    if not row:
        return {"available": False, "vram_gb": 0.0, "load": 1.0, "fresh": False,
                "reason": "hiç heartbeat yok"}
    age = float(row["age"] or 9999)
    fresh = age <= fresh_sec
    return {
        "available": bool(row["available"]) and fresh,   # bayatsa OFFLINE
        "vram_gb": float(row["vram_gb"] or 0),
        "load": float(row["load_pct"] or 0),
        "host": row["host"], "gpu_name": row["gpu_name"],
        "fresh": fresh, "age_sec": round(age, 1),
        "reason": "taze" if fresh else f"bayat ({int(age)}s > {fresh_sec}s) — PC kapalı sayılır",
    }


async def route_live(kind: str, *, complexity: str = "low", owner_email: Optional[str] = None,
                     online_budget_ok: bool = True) -> Dict[str, Any]:
    """CANLI yerel GPU durumunu çekip gpu_router ile yönlendir."""
    status = await local_gpu_status(owner_email=owner_email)
    decision = gpu_router.route_compute(kind, complexity=complexity, local_gpu=status,
                                        online_budget_ok=online_budget_ok)
    return {"decision": decision, "local_gpu": status}
