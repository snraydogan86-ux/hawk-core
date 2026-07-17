"""
FAZ 8 — Operasyonel öğrenme.

Görev sonuçlarından ÖĞRENİR: hangi provider/model/tool-sırası/retry hangi görev türünde
daha başarılı; maliyet/latency ilişkisi. Bu bilgi sonraki görev SEÇİMİNİ etkiler.

KESİN YASAK: production politikası admin ONAYI olmadan DEĞİŞMEZ. Öğrenilen tercih yalnız
(a) recommend() önerisi + (b) admin onaylı flag (HAWK_LEARN_ROUTING) açıkken devreye girer.
Yalnız log tutmaz — recommend() geçmiş başarı verisinden karar üretir (kanıtlı).
"""
from __future__ import annotations
import hashlib
import json
import os

from core.pg_memory import _get_pool


async def record(task_kind: str, dimension: str, choice_key: str, *, success: bool,
                 cost_usd: float = 0.0, latency_ms: int = 0) -> None:
    """Bir görev sonucunu kaydet (öğrenme sinyali)."""
    try:
        pool = await _get_pool()
        async with pool.acquire() as c:
            await c.execute(
                """INSERT INTO hawk_performance (task_kind,dimension,choice_key,success,cost_usd,latency_ms)
                   VALUES ($1,$2,$3,$4,$5,$6)""",
                task_kind, dimension, choice_key, bool(success), float(cost_usd), int(latency_ms))
    except Exception:
        pass


async def stats(task_kind: str, dimension: str, *, min_samples: int = 3) -> list:
    """Seçim bazında başarı oranı + örnek sayısı + ort. latency (çoktan aza)."""
    pool = await _get_pool()
    async with pool.acquire() as c:
        rows = await c.fetch(
            """SELECT choice_key,
                      count(*) n,
                      avg(CASE WHEN success THEN 1.0 ELSE 0.0 END) success_rate,
                      avg(latency_ms) avg_latency, avg(cost_usd) avg_cost
               FROM hawk_performance WHERE task_kind=$1 AND dimension=$2
               GROUP BY choice_key HAVING count(*) >= $3
               ORDER BY success_rate DESC, avg(latency_ms) ASC""",
            task_kind, dimension, min_samples)
    return [dict(r) for r in rows]


async def recommend(task_kind: str, dimension: str, *, min_samples: int = 3) -> dict:
    """Geçmiş başarıya göre EN İYİ seçim. Yetersiz veri → boş (öneri yok)."""
    s = await stats(task_kind, dimension, min_samples=min_samples)
    if not s:
        return {}
    best = s[0]
    return {"recommended": best["choice_key"], "success_rate": round(float(best["success_rate"]), 3),
            "samples": int(best["n"]), "avg_latency_ms": round(float(best["avg_latency"] or 0)),
            "alternatives": [{"choice": r["choice_key"], "success_rate": round(float(r["success_rate"]), 3),
                              "n": int(r["n"])} for r in s[1:4]]}


async def learned_provider_order(complexity: str, base_order: list) -> list:
    """HAWK_LEARN_ROUTING açıksa (admin onaylı) provider sırasını öğrenilen başarıya göre
    yeniden dizer. Default KAPALI → base_order aynen döner (production politikası değişmez)."""
    if str(os.getenv("HAWK_LEARN_ROUTING", "")).lower() not in ("1", "true", "on"):
        return base_order
    try:
        rec = await recommend(f"chat:{complexity}", "provider")
        if rec.get("recommended") and rec["recommended"] in base_order:
            r = rec["recommended"]
            return [r] + [p for p in base_order if p != r]
    except Exception:
        pass
    return base_order


async def propose_policy(task_kind: str, dimension: str, *, actor: str = "system") -> dict:
    """Öğrenilen en iyi seçimi POLİTİKA ÖNERİSİ olarak kaydet (admin onayı bekler; auto-apply YOK)."""
    rec = await recommend(task_kind, dimension)
    if not rec.get("recommended"):
        return {"ok": False, "error": "yetersiz veri"}
    pid = "pol_" + hashlib.sha256((task_kind + dimension + rec["recommended"]).encode()).hexdigest()[:16]
    pool = await _get_pool()
    async with pool.acquire() as c:
        await c.execute(
            """INSERT INTO hawk_policy_proposals (proposal_id,task_kind,dimension,recommended,evidence,status)
               VALUES ($1,$2,$3,$4,$5::jsonb,'proposed')
               ON CONFLICT (proposal_id) DO UPDATE SET evidence=EXCLUDED.evidence""",
            pid, task_kind, dimension, rec["recommended"], json.dumps(rec))
    return {"ok": True, "proposal_id": pid, **rec, "status": "proposed",
            "note": "admin onayı olmadan production'a uygulanmaz"}


async def list_proposals() -> list:
    pool = await _get_pool()
    async with pool.acquire() as c:
        rows = await c.fetch("SELECT proposal_id,task_kind,dimension,recommended,status,evidence,created_at FROM hawk_policy_proposals ORDER BY created_at DESC LIMIT 50")
    return [dict(r) for r in rows]


async def decide_proposal(proposal_id: str, approve: bool, *, actor: str = "admin") -> dict:
    pool = await _get_pool()
    async with pool.acquire() as c:
        row = await c.fetchrow(
            "UPDATE hawk_policy_proposals SET status=$2, decided_by=$3 WHERE proposal_id=$1 RETURNING task_kind,dimension,recommended",
            proposal_id, ("approved" if approve else "rejected"), actor)
    return {"ok": bool(row), "status": "approved" if approve else "rejected", **(dict(row) if row else {})}
