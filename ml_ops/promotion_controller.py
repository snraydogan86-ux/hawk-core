"""
FAZ 13 — Yeni model değerlendirme + kanıt-gate'li promotion.

AKIŞ: shadow-status model → benchmark+güvenlik değerlendirme (evaluation_gates) → shadow_results
kayıt → canary (evaluate_canary; admin-only→%1) → canary_results kayıt → PROMOTE (yalnız
gate'ler GEÇTİ + admin onayı). KESİN YASAKLAR:
  - KANIT olmadan promotion YOK (boş/başarısız shadow_results veya canary_results → block).
  - %1 kullanıcı-canary'si admin onayı ister (bu controller açmaz).
  - Otomatik production YOK; her promote admin kimliği gerektirir.
  - Kritik güvenlik ihlali → fail-safe rollback (admin beklemeden öneri).
"""
from __future__ import annotations
import json
from .evaluation_gates import evaluate_promotion
from .shadow_canary import CanaryRecord, evaluate_canary
from . import store_pg
from core.pg_memory import _get_pool


async def _set_field(model_id: str, field: str, value: dict) -> None:
    pool = await _get_pool()
    async with pool.acquire() as c:
        await c.execute(f"UPDATE public.hawk_mf_models SET {field}=$2::jsonb,updated_at=now() WHERE model_id=$1",
                        model_id, json.dumps(value))


async def record_shadow(model_id: str, *, candidate_scores: dict, safety_score: float,
                        hallucination_rate: float = 0.0, p0p1_security_all_pass: bool = False,
                        production_scores: dict | None = None,
                        system_categories_verified: bool = False, system_evidence_ref: str = "") -> dict:
    """Benchmark+güvenlik shadow değerlendirmesi çalıştırır, sonucu registry'ye yazar.
    system_categories_verified: sistem-kategorileri (workspace/multi_agent/cross_user/cross_project)
    orkestrasyon+test-suite'te enforced ise True + kanıt-ref (governance kararı)."""
    m = await store_pg.get_model(model_id)
    if not m:
        return {"ok": False, "error": "model yok"}
    gate = evaluate_promotion(candidate_scores=candidate_scores, production_scores=production_scores,
                              safety_score=safety_score, hallucination_rate=hallucination_rate,
                              p0p1_security_all_pass=p0p1_security_all_pass,
                              system_categories_verified=system_categories_verified,
                              system_evidence_ref=system_evidence_ref)
    res = {"passed": gate.passed, "blockers": gate.blockers, "warnings": gate.warnings,
           "scores": candidate_scores, "safety": safety_score}
    await _set_field(model_id, "shadow_results", res)
    return {"ok": True, "model_id": model_id, "shadow_passed": gate.passed, "blockers": gate.blockers}


async def record_canary(model_id: str, *, stage_pct: int, error_rate: float = 0.0,
                        hallucination_rate: float = 0.0, security_incidents: int = 0,
                        fallback_rate: float = 0.0, avg_latency_ms: float = 0.0,
                        cost_usd: float = 0.0) -> dict:
    """Canary metriklerini değerlendirir + registry'ye yazar. Kritik güvenlik → fail-safe."""
    rec = CanaryRecord(model_id=model_id, stage_pct=stage_pct, error_rate=error_rate,
                       hallucination_rate=hallucination_rate, security_incidents=security_incidents,
                       fallback_rate=fallback_rate, avg_latency_ms=avg_latency_ms, cost_usd=cost_usd)
    ev = evaluate_canary(rec)
    await _set_field(model_id, "canary_results", ev)
    return {"ok": True, **ev}


async def promote(model_id: str, *, admin: str, require_canary_stage: int = 1) -> dict:
    """KANIT-GATE'li promotion. shadow PASS + canary promote_ok + admin kimliği ŞART.
    Eski production → retired, rollback_target ayarlanır. Kanıt yoksa BLOCK."""
    if not admin:
        return {"ok": False, "error": "admin kimliği zorunlu (KESİN YASAK: otomatik promotion yok)"}
    try:  # FAZ 1: deployment (veya global) kill-switch → promotion/deploy DURUR
        import core.cost_guard as _cg_dep
        if _cg_dep.is_killed() or _cg_dep.is_killed("deployment"):
            return {"ok": False, "blocked": True, "error": "deployment kill-switch aktif"}
    except Exception:
        pass
    m = await store_pg.get_model(model_id)
    if not m:
        return {"ok": False, "error": "model yok"}
    sh = m.get("shadow_results") or {}
    ca = m.get("canary_results") or {}
    if isinstance(sh, str): sh = json.loads(sh or "{}")
    if isinstance(ca, str): ca = json.loads(ca or "{}")
    blockers = []
    if not sh or not sh.get("passed"):
        blockers.append("shadow_kanıt_yok_veya_başarısız")     # KANIT olmadan promotion YOK
    if not ca:
        blockers.append("canary_kanıt_yok")
    elif not ca.get("promote_ok"):
        blockers.append("canary_eşik_ihlali")
    elif int(ca.get("stage_pct") or 0) < require_canary_stage:
        blockers.append(f"canary_stage_yetersiz:{ca.get('stage_pct')}<{require_canary_stage}")
    if blockers:
        return {"ok": False, "blocked": True, "blockers": blockers}
    # gate GEÇTİ → production'a al, eskisini retired + rollback zinciri
    role = m["role"]
    old = await store_pg.production_version(role)
    pool = await _get_pool()
    async with pool.acquire() as c:
        if old and old != model_id:
            await c.execute("UPDATE public.hawk_mf_models SET status='retired',updated_at=now() WHERE model_id=$1", old)
        await c.execute("""UPDATE public.hawk_mf_models SET status='production',approved_by=$2,
                           rollback_target=$3,promoted_at=now(),updated_at=now() WHERE model_id=$1""",
                        model_id, admin, old or "")
    return {"ok": True, "promoted": model_id, "role": role, "previous_production": old,
            "rollback_target": old or None, "approved_by": admin}


async def rollback(role: str, *, admin: str) -> dict:
    """Mevcut production'ı rollback_target'a geri al (fail-safe / regresyon)."""
    if not admin:
        return {"ok": False, "error": "admin kimliği zorunlu"}
    cur = await store_pg.production_version(role)
    if not cur:
        return {"ok": False, "error": "aktif production yok"}
    m = await store_pg.get_model(cur)
    target = (m or {}).get("rollback_target")
    if not target:
        return {"ok": False, "error": "rollback_target yok"}
    pool = await _get_pool()
    async with pool.acquire() as c:
        await c.execute("UPDATE public.hawk_mf_models SET status='retired',updated_at=now() WHERE model_id=$1", cur)
        await c.execute("UPDATE public.hawk_mf_models SET status='production',rollback_by=$2,updated_at=now() WHERE model_id=$1", target, admin)
    return {"ok": True, "rolled_back_from": cur, "restored": target, "by": admin}
