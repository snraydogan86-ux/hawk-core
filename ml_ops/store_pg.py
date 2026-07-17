"""
Model Family — Postgres persistence adapter (migration 024).

Manifest-JSON registry'yi hawk_mf_* tablolarına kalıcılaştırır. asyncpg (paylaşılan pool).
GÜVENLİK: ham prompt/cevap/secret/PII yazılmaz — yalnız metadata + *_ref + content_hash.
JSONB alanlar json.dumps + ::jsonb ile; created_at float→timestamptz çevrilir.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional


async def _pool():
    from core.pg_memory import _get_pool
    return await _get_pool()


def _ts(v) -> Optional[datetime]:
    if isinstance(v, (int, float)) and v > 0:
        return datetime.fromtimestamp(v, timezone.utc)
    return None


async def _upsert(table: str, pk: str, cols: dict, jsonb: set,
                  touch_updated: bool = False) -> None:
    keys = [k for k, v in cols.items() if v is not None or k == pk]
    ph, vals = [], []
    for i, k in enumerate(keys, 1):
        if k in jsonb:
            ph.append(f"${i}::jsonb"); vals.append(json.dumps(cols[k], ensure_ascii=False))
        else:
            ph.append(f"${i}"); vals.append(cols[k])
    upd = ", ".join(f"{k}=EXCLUDED.{k}" for k in keys if k != pk)
    if touch_updated:
        upd += ", updated_at=now()"
    sql = (f"INSERT INTO {table} ({','.join(keys)}) VALUES ({','.join(ph)}) "
           f"ON CONFLICT ({pk}) DO UPDATE SET {upd}")
    pool = await _pool()
    async with pool.acquire() as c:
        await c.execute(sql, *vals)


# ---- model ----
async def upsert_model(d: dict) -> None:
    cols = {
        "model_id": d["model_id"], "family": d["family"], "role": d["role"],
        "version": d["version"], "base_model": d.get("base_model", ""),
        "adapter_type": d.get("adapter_type", "lora"), "tokenizer": d.get("tokenizer", ""),
        "training_dataset_version": d.get("training_dataset_version", ""),
        "training_job_id": d.get("training_job_id", ""), "checkpoint": d.get("checkpoint", ""),
        "quantization": d.get("quantization", ""), "context_length": d.get("context_length", 0),
        "supported_languages": list(d.get("supported_languages") or []),
        "capabilities": list(d.get("capabilities") or []),
        "benchmark_scores": d.get("benchmark_scores") or {},
        "safety_scores": d.get("safety_scores") or {}, "latency": d.get("latency") or {},
        "throughput": d.get("throughput"), "vram_gb": d.get("vram_gb"),
        "license": d.get("license", ""), "provenance": d.get("provenance", ""),
        "status": d.get("status", "draft"), "promoted_at": _ts(d.get("promoted_at")),
        "rollback_target": d.get("rollback_target", ""), "created_at": _ts(d.get("created_at")),
        # FAZ 11: provenance/bütünlük (manifest ile senkron)
        "adapter_sha256": d.get("adapter_sha256", ""), "dataset_sha256": d.get("dataset_sha256", ""),
        "training_config": d.get("training_config") or {}, "final_loss": d.get("final_loss") or {},
        "shadow_results": d.get("shadow_results") or {}, "canary_results": d.get("canary_results") or {},
        "approved_by": d.get("approved_by", ""), "rollback_by": d.get("rollback_by", ""),
    }
    await _upsert("public.hawk_mf_models", "model_id", cols,
                  {"benchmark_scores", "safety_scores", "latency", "training_config",
                   "final_loss", "shadow_results", "canary_results"}, touch_updated=True)


async def get_model(model_id: str) -> Optional[dict]:
    pool = await _pool()
    async with pool.acquire() as c:
        row = await c.fetchrow("SELECT * FROM public.hawk_mf_models WHERE model_id=$1", model_id)
    if not row:
        return None
    d = dict(row)
    for k in ("benchmark_scores", "safety_scores", "latency"):   # JSONB → dict
        if isinstance(d.get(k), str):
            try:
                d[k] = json.loads(d[k])
            except Exception:
                pass
    return d


async def production_version(role: str) -> Optional[str]:
    pool = await _pool()
    async with pool.acquire() as c:
        return await c.fetchval(
            "SELECT model_id FROM public.hawk_mf_models WHERE role=$1 AND status='production'", role)


# ---- dataset ----
async def upsert_dataset(d: dict, *, store_ref: str = "") -> None:
    cols = {
        "dataset_id": d["dataset_id"], "version": d["version"],
        "description": d.get("description", ""),
        "source_categories": list(d.get("source_categories") or []),
        "accepted_count": d.get("accepted_count", 0), "rejected_count": d.get("rejected_count", 0),
        "consent_summary": d.get("consent_summary") or {}, "pii_scan": d.get("pii_scan") or {},
        "secret_scan": d.get("secret_scan") or {}, "license_summary": d.get("license_summary") or {},
        "dedup_result": d.get("dedup_result") or {}, "split": d.get("split") or {},
        "content_hash": d.get("content_hash", ""), "manifest_hash": d.get("manifest_hash", ""),
        "store_ref": store_ref, "reviewer_approvals": list(d.get("reviewer_approvals") or []),
        "status": d.get("status", "draft"), "created_at": _ts(d.get("created_at")),
        "frozen_at": _ts(d.get("frozen_at")),
    }
    await _upsert("public.hawk_mf_datasets", "dataset_id", cols,
                  {"consent_summary", "pii_scan", "secret_scan", "license_summary",
                   "dedup_result", "split"})


# ---- training job ----
async def upsert_training_job(d: dict) -> None:
    cols = {
        "training_job_id": d["training_job_id"], "model_target": d["model_target"],
        "base_model": d["base_model"], "dataset_version": d["dataset_version"],
        "method": d.get("method", "lora"), "lora_config": d.get("lora_config") or {},
        "learning_rate": d.get("learning_rate", 0.0001),
        "sequence_length": d.get("sequence_length", 4096), "batch_size": d.get("batch_size", 4),
        "gradient_accumulation": d.get("gradient_accumulation", 4), "epochs": d.get("epochs", 1),
        "seed": d.get("seed", 42), "hardware": d.get("hardware", ""),
        "estimated_cost": d.get("estimated_cost", 0), "hard_cost_limit": d["hard_cost_limit"],
        "max_runtime_s": d.get("max_runtime_s", 0),
        "checkpoint_interval": d.get("checkpoint_interval", 0),
        "output_location": d.get("output_location", ""), "status": d.get("status", "draft"),
        "logs_ref": d.get("logs_ref", ""), "metrics": d.get("metrics") or {},
        "admin_approved_by": d.get("admin_approved_by", ""),
        "gpu_approved_by": d.get("gpu_approved_by", ""),
        "started_at": _ts(d.get("started_at")), "completed_at": _ts(d.get("completed_at")),
        "failure_reason": d.get("failure_reason", ""),
    }
    await _upsert("public.hawk_mf_training_jobs", "training_job_id", cols,
                  {"lora_config", "metrics"}, touch_updated=True)


# ---- candidate metadata (HAM METİN YOK) ----
async def upsert_candidate_meta(*, candidate_id: str, dataset_id: str, source_type: str,
                                role: str, polarity: str, content_hash: str,
                                input_ref: str, output_ref: str, consent_status: str,
                                pii_status: str, secret_status: str, license_status: str,
                                provenance: str, quality_score: float, reviewer_score: float,
                                safety_score: float, accepted: bool,
                                source_task_hash: str = "", source_agent_run_hash: str = "") -> None:
    cols = {
        "candidate_id": candidate_id, "dataset_id": dataset_id, "source_type": source_type,
        "role": role, "polarity": polarity, "content_hash": content_hash,
        "input_ref": input_ref, "output_ref": output_ref, "consent_status": consent_status,
        "pii_status": pii_status, "secret_status": secret_status, "license_status": license_status,
        "provenance": provenance, "quality_score": quality_score, "reviewer_score": reviewer_score,
        "safety_score": safety_score, "accepted": accepted,
        "source_task_hash": source_task_hash, "source_agent_run_hash": source_agent_run_hash,
    }
    await _upsert("public.hawk_mf_dataset_candidates", "candidate_id", cols, set())


import hashlib as _hashlib


def user_source_hash(user_key: str) -> str:
    """Kullanıcı-kaynak hash'i (düz email/id ASLA saklanmaz). B6 candidate'ı bu hash'le
    kaydeder, B8 forget bununla revoke eder. Tek-yönlü, tuzlu."""
    return _hashlib.sha256(("hawk-src:" + str(user_key or "")).encode()).hexdigest()


async def revoke_candidates_for_user(user_key: str) -> dict:
    """FORGET/KVKK: kullanıcının kaynak-hash'ine ait candidate'ları işaretle.
    - accepted DEĞİL → 'revoked' (gelecekteki build'e girmez)
    - accepted (frozen) → 'deleted' (bir sonraki dataset sürümünden çıkarılır)
    Düz kişisel veri YAZILMAZ. Döner: {revoked, deleted}."""
    h = user_source_hash(user_key)
    try:
        pool = await _pool()
        async with pool.acquire() as c:
            res = await c.execute(
                "UPDATE public.hawk_mf_dataset_candidates "
                "SET deletion_status = CASE WHEN accepted THEN 'deleted' ELSE 'revoked' END "
                "WHERE (source_task_hash = $1 OR source_agent_run_hash = $1) AND deletion_status = ''",
                h)
        n = int(str(res).split()[-1]) if str(res).split() else 0
        return {"matched_hash": h[:12], "affected": n}
    except Exception as e:
        return {"error": str(e)[:120], "affected": 0}


async def counts() -> dict:
    pool = await _pool()
    async with pool.acquire() as c:
        out = {}
        for t in ("hawk_mf_models", "hawk_mf_datasets", "hawk_mf_training_jobs",
                  "hawk_mf_dataset_candidates"):
            out[t] = await c.fetchval(f"SELECT count(*) FROM public.{t}")
        return out
