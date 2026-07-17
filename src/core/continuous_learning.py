"""
FAZ 20 — Genel kullanıcı sürekli öğrenme.

İzinli (FAZ 10 consent) kullanıcı etkileşimlerinden SÜREKLİ aday toplar → governance gate
(PII/secret/kalite/dedup/reviewer) → aday havuzu (consent=pending). Öğrenilen modeli genel
kullanıcı trafiğine (%1) açmak FAZ 14 gate'iyle admin ONAYINDA DURUR.

KESİN YASAKLAR (kalıcı):
  - İzin vermeyen kullanıcıdan veri alınmaz (FAZ 10).
  - Ham kullanıcı verisi DOĞRUDAN ağırlığa gitmez (yalnız governance-gate'li aday, accepted=False).
  - %1 kullanıcı-trafiği açılışı admin onayı + registry-production ister (bu modül AÇMAZ).
  - Geri-çekilebilir (kullanıcı silince aday revoke — B8).
"""
from __future__ import annotations
from core.pg_memory import _get_pool


async def harvest(user_key: str, *, source_type: str, objective: str, output_text: str,
                  review_passed: bool = True) -> dict:
    """Tek etkileşimi SADECE consent varsa governance-gate'li aday olarak öner (FAZ 10)."""
    from core import user_consent as uc
    return await uc.capture_if_consented(user_key, source_type=source_type, objective=objective,
                                         output_text=output_text, review_passed=review_passed)


async def pipeline_status() -> dict:
    """Sürekli öğrenme hattı durumu: consent sayısı, aday havuzu, kabul/red, trafik gate."""
    pool = await _get_pool()
    async with pool.acquire() as c:
        consents = await c.fetchval("SELECT count(*) FROM hawk_user_consent WHERE granted=true") or 0
        reg = await c.fetchval("SELECT to_regclass('public.hawk_mf_dataset_candidates')")
        pending = accepted = rejected = 0
        if reg:
            pending = await c.fetchval("SELECT count(*) FROM hawk_mf_dataset_candidates WHERE consent_status IN ('pending','granted') AND accepted=false") or 0
            accepted = await c.fetchval("SELECT count(*) FROM hawk_mf_dataset_candidates WHERE accepted=true") or 0
            rejected = await c.fetchval("SELECT count(*) FROM hawk_mf_dataset_candidates WHERE rejection_reason<>''") or 0
    # genel trafik gate (FAZ 14): production model var mı + canary genel-açık mı
    from core.model_family import store_pg as sp
    prod = await sp.production_version("hawk_base")
    return {
        "consented_users": int(consents),
        "candidate_pool": {"pending_review": int(pending), "accepted": int(accepted), "rejected": int(rejected)},
        "raw_to_weights": False,                         # ham veri asla ağırlığa gitmez
        "traffic_gate": {
            "registry_production_model": prod or None,
            "general_rollout_ready": bool(prod),          # promotion (FAZ 13) tamamlandı mı
            "general_rollout_open": False,                # açılış admin onayı ister (FAZ 14)
            "note": "%1 kullanıcı trafiği admin onayı + registry-production olmadan açılmaz",
        },
    }


async def request_general_rollout(pct: float, *, admin: str) -> dict:
    """%1 genel rollout TALEBİ. KESİN YASAK: bu modül trafiği AÇMAZ — yalnız ön-koşulları
    doğrular ve admin'i FAZ 14 canary akışına yönlendirir (açık admin adımı şart)."""
    if not admin:
        return {"ok": False, "error": "admin kimliği zorunlu"}
    from core.model_family import store_pg as sp
    prod = await sp.production_version("hawk_base")
    if not prod:
        return {"ok": False, "blocked": True,
                "reason": "registry_production_yok — önce FAZ 13 promotion (kanıt-gate'li)"}
    return {"ok": True, "prerequisites_met": True, "opened": False,
            "next_step": "POST /api/admin/hawkbase/canary action=stage stage=1 (açık admin adımı)",
            "note": "Ön-koşullar hazır ama trafik OTOMATİK AÇILMAZ — admin canary adımını atmalı (KESİN YASAK)."}
