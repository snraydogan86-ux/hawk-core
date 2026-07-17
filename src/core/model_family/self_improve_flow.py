"""
HAWK kontrollü self-improvement — CANLI ajan görevi → dataset CANDIDATE (B6).

Zincir: agent task → evidence → reviewer → (redaction + consent) → dataset candidate → admin review.

KESİN YASAKLAR (uygulanır):
  - consent=pending → aday ASLA otomatik kabul edilmez (admin onayı şart).
  - PII/secret redakte edilir; secret varsa safety düşer.
  - source_agent_run_hash = user_source_hash → kullanıcı 'unut' derse (B8) revoked olur.
  - accepted=False (production ağırlığı/dataset sürümü OTOMATİK değişmez).
Yalnız ÖNERİ üretir; eğitim/promotion admin onayı bekler.
"""
from __future__ import annotations
import hashlib
from core.agent_orchestration.dataset import redact
from . import store_pg

CANDIDATE_POOL_ID = "hawk-candidate-pool"


async def _ensure_pool():
    await store_pg.upsert_dataset({
        "dataset_id": CANDIDATE_POOL_ID, "version": "live",
        "description": "Canlı ajan görevlerinden governance candidate havuzu (admin review bekler)."})


async def propose_from_task(*, user_scope: str, source_type: str, objective: str,
                            output_text: str, review_passed: bool, source_task_id: str = "") -> dict:
    """Tamamlanmış+denetlenmiş görevi governance CANDIDATE'ına çevir (redakte, consent=pending)."""
    out_red, had_pii, had_secret = redact(str(output_text or ""))
    in_red, in_pii, in_secret = redact(str(objective or ""))
    had_pii = had_pii or in_pii
    had_secret = had_secret or in_secret
    content_hash = hashlib.sha256((in_red + "→" + out_red).encode()).hexdigest()
    cid = "cand_" + content_hash[:20]
    try:
        await _ensure_pool()
        await store_pg.upsert_candidate_meta(
            candidate_id=cid, dataset_id=CANDIDATE_POOL_ID, source_type=source_type,
            role="hawk_base", polarity="positive", content_hash=content_hash,
            input_ref=in_red[:400], output_ref=out_red[:400],
            consent_status="pending",                    # onay YOK → asla otomatik kabul
            pii_status=("redacted" if had_pii else "clean"),
            secret_status=("redacted" if had_secret else "clean"),
            license_status="pending", provenance="live_agent_task",
            quality_score=(0.7 if review_passed else 0.3),
            reviewer_score=(1.0 if review_passed else 0.0),
            safety_score=(0.2 if had_secret else 0.9),
            accepted=False,                              # KESİN: otomatik kabul YOK
            source_task_hash=(hashlib.sha256(("t:" + str(source_task_id)).encode()).hexdigest()
                              if source_task_id else ""),
            source_agent_run_hash=store_pg.user_source_hash(user_scope))
        return {"ok": True, "candidate_id": cid, "consent": "pending", "accepted": False,
                "pii_redacted": had_pii, "secret_redacted": had_secret}
    except Exception as e:
        return {"ok": False, "error": str(e)[:150]}
