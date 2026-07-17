"""
HAWK AI Economy Manager — GPU cloud/model harcamasını izler ve bütçe kararları verir.

İzler: günlük & saatlik harcama, token kullanımı, ortalama cevap maliyeti,
istek sayısı. Bütçe DAILY_BUDGET_USD (.env, varsayılan 5 USD) üzerinden.

Bütçe eşikleri:
  %50  → bilgi
  %80  → gereksiz Tier3'ü azalt (plan'a göre)
  %100 → Free: Tier3 kapalı; Plus: yalnız kritik; Gold: devam (loglanır)

Bu modül salt-karar + log üretir; hiçbir kritik işlem yürütmez.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

# Tier başına 1k-token tahmini maliyet (USD) — .env ile ayarlanabilir
COST_PER_1K = {
    "tier1": float(os.getenv("COST_TIER1_USD", "0.0001")),
    "tier2": float(os.getenv("COST_TIER2_USD", "0.0006")),
    "tier3": float(os.getenv("COST_TIER3_USD", "0.0015")),
}


def daily_budget() -> float:
    try:
        return float(os.getenv("DAILY_BUDGET_USD", "5") or "5")
    except Exception:
        return 5.0


def estimate_cost(tier: str, tokens: int) -> float:
    return round(COST_PER_1K.get(tier, COST_PER_1K["tier3"]) * (max(0, tokens) / 1000.0), 6)


async def _pool():
    from db import get_pool
    return await get_pool()


async def _spend(interval_sql: str) -> Dict[str, Any]:
    try:
        pool = await _pool()
        async with pool.acquire() as con:
            row = await con.fetchrow(
                f"""SELECT COALESCE(SUM(GREATEST(real_cost, estimated_cost)),0) AS spend,
                           COALESCE(SUM(saved_cost),0) AS saved,
                           COUNT(*) AS calls
                    FROM public.model_call_logs
                    WHERE created_at >= now() - interval '{interval_sql}'""")
            return {"spend": float(row["spend"]), "saved": float(row["saved"]), "calls": int(row["calls"])}
    except Exception:
        return {"spend": 0.0, "saved": 0.0, "calls": 0}


async def budget_status() -> Dict[str, Any]:
    budget = daily_budget()
    today = await _spend("1 day")
    hour = await _spend("1 hour")
    pct = round((today["spend"] / budget) * 100, 1) if budget > 0 else 0.0
    level = "ok"
    if pct >= 100:
        level = "exceeded"
    elif pct >= 80:
        level = "critical"
    elif pct >= 50:
        level = "info"
    return {"budget_usd": budget, "today_spend": round(today["spend"], 6),
            "hourly_spend": round(hour["spend"], 6), "today_calls": today["calls"],
            "today_saved": round(today["saved"], 6), "pct": pct, "level": level}


async def decide(plan: str, requested_tier: str, *, critical: bool = False) -> Dict[str, Any]:
    """Bütçe + plan'a göre izin verilen tier'ı döndürür. Tier3 pahalı kabul edilir."""
    plan = (plan or "free").lower()

    # F5: SERT DURDURMA — kill switch veya günlük sert bütçe aşıldıysa gerçekten blokla.
    try:
        from core import cost_guard as _cg
        _pf = await _cg.preflight()
        if _pf.get("blocked"):
            _st = await budget_status()
            return {"requested_tier": requested_tier, "allowed_tier": "tier1",
                    "downgraded": True, "blocked": True,
                    "reason": f"HARD STOP: {_pf.get('reason')}", "budget": _st}
    except Exception:
        pass

    st = await budget_status()
    pct, level = st["pct"], st["level"]

    allowed = requested_tier
    reason = f"budget {pct}% ({level})"

    def downgrade(to):
        return to if _rank(to) < _rank(requested_tier) else requested_tier

    if level in ("ok", "info"):
        allowed = requested_tier  # serbest
    elif level == "critical":  # %80+
        if requested_tier == "tier3":
            if plan == "free":
                allowed = "tier2"; reason = f"%80+ bütçe: Free için Tier3 kapalı ({pct}%)"
            elif plan == "plus" and not critical:
                allowed = "tier2"; reason = f"%80+ bütçe: Plus için Tier3 yalnız kritik ({pct}%)"
            else:
                reason = f"%80+ bütçe: {plan} için Tier3 izinli (loglanır) ({pct}%)"
    elif level == "exceeded":  # %100+
        if plan == "free":
            allowed = "tier1"; reason = f"%100 bütçe: Free → küçük model ({pct}%)"
        elif plan == "plus":
            allowed = downgrade("tier2"); reason = f"%100 bütçe: Plus → orta model ({pct}%)"
        else:  # gold
            if requested_tier == "tier3" and not critical:
                allowed = "tier2"; reason = f"%100 bütçe: Gold Tier3 yalnız onay/kritik işlerde ({pct}%)"
            else:
                reason = f"%100 bütçe: Gold kritik Tier3 izinli ({pct}%)"

    return {"requested_tier": requested_tier, "allowed_tier": allowed,
            "downgraded": allowed != requested_tier, "blocked": False,
            "reason": reason, "budget": st}


def _rank(tier: str) -> int:
    return {"tier1": 1, "tier2": 2, "tier3": 3}.get(tier, 2)


async def record_call(*, user_id: str = "", user_plan: str = "free", request_hash: str = "",
                      intent: str = "", tier: str = "tier1", model: str = "", reason: str = "",
                      complexity_score: int = 0, escalated: bool = False, escalations: int = 0,
                      cache_hit: bool = False, estimated_cost: float = 0.0, real_cost: float = 0.0,
                      saved_cost: float = 0.0, latency_ms: int = 0) -> Optional[int]:
    try:
        pool = await _pool()
        async with pool.acquire() as con:
            row = await con.fetchrow(
                """INSERT INTO public.model_call_logs
                   (user_id,user_plan,request_hash,intent,tier,model,reason,complexity_score,
                    escalated,escalations,cache_hit,estimated_cost,real_cost,saved_cost,latency_ms)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15) RETURNING id""",
                user_id, user_plan, request_hash, intent, tier, model, reason, complexity_score,
                escalated, escalations, cache_hit, estimated_cost, real_cost, saved_cost, latency_ms)
            return row["id"]
    except Exception:
        return None


async def stats_24h() -> Dict[str, Any]:
    try:
        pool = await _pool()
        async with pool.acquire() as con:
            row = await con.fetchrow(
                """SELECT COUNT(*) AS calls,
                          COALESCE(AVG(GREATEST(real_cost,estimated_cost)),0) AS avg_cost,
                          COALESCE(AVG(latency_ms),0) AS avg_latency,
                          COALESCE(SUM(escalations),0) AS escalations,
                          COUNT(*) FILTER (WHERE cache_hit) AS cache_hits,
                          COUNT(*) FILTER (WHERE tier='tier3') AS tier3_calls
                   FROM public.model_call_logs
                   WHERE created_at >= now() - interval '1 day'""")
            calls = int(row["calls"])
            return {"calls_24h": calls, "avg_cost": round(float(row["avg_cost"]), 6),
                    "avg_latency_ms": int(row["avg_latency"]), "escalations": int(row["escalations"]),
                    "cache_hits": int(row["cache_hits"]),
                    "cache_hit_rate": round(row["cache_hits"] / calls, 3) if calls else 0.0,
                    "tier3_calls": int(row["tier3_calls"])}
    except Exception:
        return {"calls_24h": 0, "avg_cost": 0.0, "avg_latency_ms": 0, "escalations": 0,
                "cache_hits": 0, "cache_hit_rate": 0.0, "tier3_calls": 0}
