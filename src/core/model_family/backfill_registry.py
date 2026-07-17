#!/usr/bin/env python3
"""
Manifest-JSON registry'yi hawk_mf_* Postgres tablolarına backfill eder (migration 024 sonrası).
Idempotent (upsert). Ham metin DB'ye GİRMEZ — candidate'lar için yalnız content_hash + ref.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os

from core.model_family import store_pg as S

HERE = os.path.dirname(__file__)
REG = os.path.join(HERE, "registry")
CAND = os.path.join(REG, "candidates")


async def main() -> None:
    models = datasets = jobs = cands = 0
    # manifestler
    for fn in sorted(os.listdir(REG)):
        if not fn.endswith(".json"):
            continue
        d = json.load(open(os.path.join(REG, fn), encoding="utf-8"))
        if "model_id" in d and "role" in d:
            await S.upsert_model(d); models += 1
        elif "training_job_id" in d:
            await S.upsert_training_job(d); jobs += 1
        elif "dataset_id" in d:
            store = os.path.join("registry/candidates", f"{d['dataset_id']}.jsonl")
            await S.upsert_dataset(d, store_ref=store if os.path.exists(os.path.join(HERE, store)) else "")
            datasets += 1

    # candidate metadata (kabul depolarından — HAM METİN YOK, yalnız hash+ref)
    if os.path.isdir(CAND):
        for fn in sorted(os.listdir(CAND)):
            if not fn.endswith(".jsonl") or fn.startswith("additions"):
                continue
            dataset_id = fn[:-6]  # hawk-dataset-v0.2
            ref_base = f"registry/candidates/{fn}"
            for line in open(os.path.join(CAND, fn), encoding="utf-8"):
                s = line.strip()
                if not s or s.startswith(("#", "//")):
                    continue
                c = json.loads(s)
                ch = hashlib.sha256((c.get("input", "") + "|" + c.get("output", "")).encode()).hexdigest()[:16]
                await S.upsert_candidate_meta(
                    candidate_id=c["candidate_id"], dataset_id=dataset_id,
                    source_type=c.get("source_type", ""), role=c.get("role", ""),
                    polarity=c.get("polarity", "positive"), content_hash=ch,
                    input_ref=f"{ref_base}#{c['candidate_id']}", output_ref=f"{ref_base}#{c['candidate_id']}",
                    consent_status=c.get("consent", "unknown"), pii_status="clean",
                    secret_status="clean", license_status=c.get("license", "unknown"),
                    provenance=c.get("provenance", ""), quality_score=c.get("quality", 0.0),
                    reviewer_score=c.get("reviewer_score", 0.0), safety_score=c.get("safety", 0.0),
                    accepted=True)
                cands += 1

    print(f"backfill: models={models} datasets={datasets} jobs={jobs} candidates={cands}")
    print(f"DB sayımları: {await S.counts()}")
    for role in ("hawk_base", "hawk_code", "hawk_mini", "hawk_reasoning", "hawk_vision"):
        pv = await S.production_version(role)
        print(f"  production_version({role}) = {pv} (None olmalı)")


if __name__ == "__main__":
    asyncio.run(main())
