"""
HAWK Online GPU yaşam döngüsü — maliyet-güvenli start/stop + otomatik kapanma (Md.9/14).

- ensure_online(): aylık bütçe uygunsa (can_spend_gpu) online GPU'yu başlat (gate'li).
- touch_activity(): online GPU iş aldıkça çağrılır (idle sayacını sıfırlar).
- maybe_shutdown(): iş yok + idle eşiği aşıldıysa online GPU'yu DURDUR + uptime maliyeti işle.
Böylece online GPU sürekli açık kalmaz, maliyet tavanı aşılmaz.
"""
from __future__ import annotations

from typing import Any, Dict

from core.pg_memory import _get_pool
from core import gpu_router, gpu_providers


async def _monthly_spent() -> float:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT coalesce(sum(est_cost_usd),0) AS s FROM hawk_gpu_online "
            "WHERE date_trunc('month', coalesce(stopped_at, updated_at)) = date_trunc('month', now())")
    return float(row["s"] or 0)


async def online_status() -> Dict[str, Any]:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT provider, ref, status, started_at, last_activity, est_cost_usd, "
            "EXTRACT(EPOCH FROM (now()-last_activity)) AS idle_sec "
            "FROM hawk_gpu_online ORDER BY id DESC LIMIT 1")
    return {"instance": (dict(row) if row else None),
            "monthly_spent_usd": await _monthly_spent(),
            "provider_status": gpu_providers.provider_status()}


async def ensure_online(*, est_hours: float = 1.0) -> Dict[str, Any]:
    """Online GPU'yu başlat (aylık bütçe + gate kontrolü). GATE kapalıysa dry-run."""
    est_cost = round(est_hours * gpu_providers.ONLINE_USD_PER_HOUR, 4)
    spend = gpu_router.can_spend_gpu(await _monthly_spent(), est_cost)
    if not spend["allow"]:
        return {"ok": False, "reason": spend["reason"], "cap_remaining": spend.get("remaining")}

    res = await gpu_providers.start_online()
    pool = await _get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO hawk_gpu_online (provider, ref, status, usd_per_hour, started_at, last_activity, meta)
               VALUES ('gpu_cloud', $1, $2, $3, now(), now(), $4)""",
            gpu_providers.ONLINE_POD_ID or "dry-run",
            ("running" if res.get("ok") and not res.get("dry_run") else "dry_run"),
            gpu_providers.ONLINE_USD_PER_HOUR, __import__("json").dumps(res))
    return {"ok": True, "start_result": res, "est_cost_usd": est_cost}


async def touch_activity() -> None:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE hawk_gpu_online SET last_activity=now(), updated_at=now() "
            "WHERE status IN ('running','idle','dry_run') "
            "AND id=(SELECT id FROM hawk_gpu_online ORDER BY id DESC LIMIT 1)")


async def maybe_shutdown(*, idle_threshold: int = gpu_router.IDLE_SHUTDOWN_SEC,
                         active_jobs: int = 0) -> Dict[str, Any]:
    """İş yok + idle eşiği aşıldıysa online GPU'yu durdur + uptime maliyeti işle."""
    pool = await _get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, started_at, usd_per_hour, EXTRACT(EPOCH FROM (now()-last_activity)) AS idle, "
            "EXTRACT(EPOCH FROM (now()-started_at)) AS uptime "
            "FROM hawk_gpu_online WHERE status IN ('running','idle','dry_run') ORDER BY id DESC LIMIT 1")
    if not row:
        return {"shutdown": False, "reason": "aktif online GPU yok"}

    decision = gpu_router.should_shutdown_online(
        idle_seconds=float(row["idle"] or 0), active_jobs=active_jobs, idle_threshold=idle_threshold)
    if not decision["shutdown"]:
        return {"shutdown": False, "reason": decision["reason"]}

    res = await gpu_providers.stop_online()
    cost = round(float(row["uptime"] or 0) / 3600.0 * float(row["usd_per_hour"] or 0), 4)
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE hawk_gpu_online SET status='stopped', stopped_at=now(), est_cost_usd=$2, updated_at=now() WHERE id=$1",
            row["id"], cost)
    return {"shutdown": True, "reason": decision["reason"], "stop_result": res, "est_cost_usd": cost}
