#!/usr/bin/env python3
"""
hawk-dataset-v0.3 üretir = v0.1 seed + v0.2 eklemeler + v0.3 kürasyonlu EKLEMELER.
GPU'suz, production verisi YOK. Frozen v0.1/v0.2'ye DOKUNMAZ (yeni sürüm).
16-adım governance (consent/PII/secret/lisans/reviewer/kalite/admin) her adaya uygulanır.
"""
from __future__ import annotations

import json
import os
import time

from core.model_family import SeedSource
from core.model_family.expand import DatasetExpander, FileSource

HERE = os.path.dirname(__file__)
REG = os.path.join(HERE, "registry")
ADD2 = os.path.join(REG, "candidates", "additions_v0.2.jsonl")
ADD3 = os.path.join(REG, "candidates", "additions_v0.3.jsonl")
STORE = os.path.join(REG, "candidates", "hawk-dataset-v0.3.jsonl")
MF = os.path.join(REG, "hawk-dataset-v0.3.json")


def main() -> None:
    exp = DatasetExpander()
    exp.add(SeedSource())               # v0.1 temeli
    exp.add(FileSource(ADD2))           # v0.2 eklemeleri
    exp.add(FileSource(ADD3))           # v0.3 kürasyonlu eklemeler
    summary = exp.build(
        dataset_id="hawk-dataset-v0.3", version="v0.3", admin_approved=True,
        reviewer_approvals=("soner",), store_path=STORE, now=time.time(),
        source_categories=("nl", "tool", "workspace", "code", "security", "forget",
                           "json", "reasoning", "memory"),
        description="HAWK Base v0.3 — v0.2 + kimlik/tool/güvenlik/reasoning/memory eklemeleri.")
    dv = exp.pipe.registry.get("hawk-dataset-v0.3")
    json.dump(dv.to_dict(), open(MF, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    print("=== hawk-dataset-v0.3 üretildi (frozen) ===")
    print(f" kabul: {summary['accepted']} | red: {summary['rejected']} | nedenler: {summary['reject_reasons']}")
    print(f" denge: {exp.balance()}")
    print(f" kapsama: {exp.coverage()}")
    print(f" HEDEFE göre kalan BOŞLUKLAR: {exp.gaps()}")
    print(f" depo: {STORE} ({exp.save_store(STORE)} kabul edilmiş redakte aday)")
    print(f" manifest: {MF} | content_hash: {dv.content_hash}")


if __name__ == "__main__":
    main()
