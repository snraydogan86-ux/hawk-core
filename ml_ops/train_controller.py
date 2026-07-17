"""
FAZ 12 — Kontrollü model eğitimi (KALICI + gate'li).

DOKTRIN (KESİN YASAKLAR):
  - Admin ONAYSIZ training job BAŞLAMAZ.
  - Paid GPU AYRI onay + hard_cost_limit; controller ücretli pod'u KENDİLİĞİNDEN AÇMAZ
    (READY'de DURUR; fiili launch admin'in manuel adımı).
  - Dataset FROZEN + SHA256 sabit olmadan eğitim yok.
  - Sonuç registry'ye status='trained' (shadow) yazılır — ASLA otomatik production.
  - Kanıtsız/onaysız promotion YOK.

training_job.py'nin TrainingRegistry state-machine'ini DOĞRULAMA için kullanır, kararı
PostgreSQL'e (hawk_mf_training_jobs) yazar; kill-scope 'training' + bütçe kapısı uygulanır.
"""
from __future__ import annotations
import hashlib
import json
from .training_job import TrainingJob, TrainingRegistry, JobStatus, TrainingError
from . import store_pg
from core.pg_memory import _get_pool


def _dataset_sha(dataset_version: str, content_hash: str) -> str:
    return content_hash or hashlib.sha256(("ds:" + dataset_version).encode()).hexdigest()


async def _get_job_row(jid: str) -> dict | None:
    pool = await _get_pool()
    async with pool.acquire() as c:
        r = await c.fetchrow("SELECT * FROM public.hawk_mf_training_jobs WHERE training_job_id=$1", jid)
    return dict(r) if r else None


async def _budget_ok() -> tuple[bool, str]:
    """Eğitim kill-scope + günlük bütçe kapısı (FAZ 3)."""
    try:
        import core.cost_guard as cg
        if cg.is_killed("training") or cg.is_killed():
            return False, "training kill-switch aktif"
        if hasattr(cg, "budget_blocked") and await cg.budget_blocked("training"):
            return False, "günlük bütçe aşıldı"
    except Exception:
        pass
    return True, ""


async def propose(*, target_version: str, base_model: str, dataset_version: str,
                  dataset_content_hash: str, config: dict, hard_cost_limit: float,
                  hardware: str = "gpu_cloud-gpu", est_cost: float = 0.0) -> dict:
    """Yeni eğitim ÖNERİSİ (pending_approval). Dataset SHA256 sabitlenir. OTOMATİK BAŞLAMAZ."""
    if hard_cost_limit <= 0:
        return {"ok": False, "error": "hard_cost_limit > 0 zorunlu"}
    jid = "tj_" + hashlib.sha256((target_version + dataset_version + str(config)).encode()).hexdigest()[:16]
    ds_sha = _dataset_sha(dataset_version, dataset_content_hash)
    reg = TrainingRegistry()   # state-machine doğrulaması
    job = TrainingJob(training_job_id=jid, model_target=f"hawk-base-{target_version}",
                      base_model=base_model, dataset_version=dataset_version,
                      hardware=hardware, estimated_cost=est_cost, hard_cost_limit=hard_cost_limit,
                      lora_config=dict(config))
    reg.create(job)   # → PENDING_APPROVAL (hard_cost_limit>0 doğrular)
    d = job.to_dict()
    d["metrics"] = {"dataset_sha256": ds_sha, "config": config}
    await store_pg.upsert_training_job(d)
    return {"ok": True, "training_job_id": jid, "status": job.status.value,
            "dataset_sha256": ds_sha, "note": "admin onayı bekliyor (KESİN YASAK: onaysız başlamaz)"}


async def _advance(jid: str, *, expect: str, to: str, field: str, actor: str) -> dict:
    if not actor:
        return {"ok": False, "error": "admin kimliği zorunlu"}
    row = await _get_job_row(jid)
    if not row:
        return {"ok": False, "error": "job yok"}
    if row["status"] != expect:
        return {"ok": False, "error": f"geçersiz durum: {row['status']} (beklenen {expect})"}
    pool = await _get_pool()
    async with pool.acquire() as c:
        await c.execute(f"UPDATE public.hawk_mf_training_jobs SET status=$2,{field}=$3,updated_at=now() WHERE training_job_id=$1",
                        jid, to, actor)
    return {"ok": True, "status": to, field: actor}


async def approve(jid: str, *, admin: str) -> dict:
    """Eğitim onayı → pending_gpu_approval (GPU AYRI onay)."""
    return await _advance(jid, expect="pending_approval", to="pending_gpu_approval",
                          field="admin_approved_by", actor=admin)


async def approve_gpu(jid: str, *, admin: str, gpu_report: dict | None = None) -> dict:
    """Paid GPU onayı → ready. Controller pod'u AÇMAZ; READY'de DURUR (manuel launch)."""
    ok, why = await _budget_ok()
    if not ok:
        return {"ok": False, "error": why}
    r = await _advance(jid, expect="pending_gpu_approval", to="ready",
                       field="gpu_approved_by", actor=admin)
    if r.get("ok"):
        r["note"] = "READY — ücretli pod controller tarafından AÇILMAZ; fiili launch manuel (hard_cost_limit uygulanır)"
        r["gpu_report"] = gpu_report or {}
    return r


async def record_result(jid: str, *, final_loss: dict, eval_scores: dict, actor: str = "system") -> dict:
    """Harici (onaylı) eğitim tamamlandığında sonucu KAYDET. Registry'ye status='trained'
    (shadow) yazılır — ASLA production. Promotion ayrı, kanıt-gate'li akıştır (FAZ 13)."""
    row = await _get_job_row(jid)
    if not row:
        return {"ok": False, "error": "job yok"}
    if row["status"] not in ("ready", "running"):
        return {"ok": False, "error": f"job {row['status']} — sonuç kaydı için ready/running gerekir"}
    pool = await _get_pool()
    async with pool.acquire() as c:
        await c.execute("UPDATE public.hawk_mf_training_jobs SET status='completed',metrics=metrics||$2::jsonb,completed_at=now() WHERE training_job_id=$1",
                        jid, json.dumps({"final_loss": final_loss, "eval": eval_scores}))
    # registry'ye trained model (shadow, NOT production)
    target = row["model_target"]
    version = target.replace("hawk-base-", "")
    ds_sha = (row.get("metrics") or {})
    if isinstance(ds_sha, str):
        try: ds_sha = json.loads(ds_sha)
        except Exception: ds_sha = {}
    await store_pg.upsert_model({
        "model_id": target, "family": "base", "role": "hawk_base", "version": version,
        "base_model": row["base_model"], "adapter_type": "qlora",
        "training_dataset_version": row["dataset_version"], "training_job_id": jid,
        "dataset_sha256": ds_sha.get("dataset_sha256", ""), "training_config": ds_sha.get("config", {}),
        "final_loss": final_loss, "status": "shadow",   # KESİN YASAK: otomatik production YOK
        "provenance": f"train_controller job={jid}", "approved_by": row.get("admin_approved_by", ""),
    })
    return {"ok": True, "status": "completed", "model_id": target, "registry_status": "shadow",
            "note": "production DEĞİL — promotion kanıt-gate'li ayrı akış (FAZ 13)"}


async def get(jid: str) -> dict | None:
    return await _get_job_row(jid)
