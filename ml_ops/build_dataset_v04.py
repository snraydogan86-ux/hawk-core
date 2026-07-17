#!/usr/bin/env python3
"""
hawk-dataset-v0.4 üretir = v0.1 seed + v0.2 + v0.3 + v0.4 kürasyonlu EKLEMELER.
GPU'suz, production verisi YOK. Önceki frozen sürümlere DOKUNMAZ.
16-adım governance her adaya uygulanır (consent/PII/secret/lisans/reviewer/kalite/admin/dedup).
"""
from __future__ import annotations

import json
import os
import time

from core.model_family import SeedSource
from core.model_family.expand import DatasetExpander, FileSource

HERE = os.path.dirname(__file__)
REG = os.path.join(HERE, "registry")
ADDS = [os.path.join(REG, "candidates", f"additions_v0.{v}.jsonl") for v in (2, 3, 4)]
STORE = os.path.join(REG, "candidates", "hawk-dataset-v0.4.jsonl")
MF = os.path.join(REG, "hawk-dataset-v0.4.json")


def main() -> None:
    exp = DatasetExpander()
    exp.add(SeedSource())
    for p in ADDS:
        exp.add(FileSource(p))
    summary = exp.build(
        dataset_id="hawk-dataset-v0.4", version="v0.4", admin_approved=True,
        reviewer_approvals=("soner",), store_path=STORE, now=time.time(),
        source_categories=("nl", "tool", "workspace", "code", "security", "forget",
                           "json", "reasoning", "memory"),
        description="HAWK Base v0.4 — v0.3 + 174 kürasyonlu ekleme (tüm kategoriler dengelendi).")
    dv = exp.pipe.registry.get("hawk-dataset-v0.4")
    json.dump(dv.to_dict(), open(MF, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    print("=== hawk-dataset-v0.4 üretildi (frozen) ===")
    print(f" kabul: {summary['accepted']} | red: {summary['rejected']} | nedenler: {summary['reject_reasons']}")
    print(f" denge: {exp.balance()}")
    print(f" kapsama: {exp.coverage()}")
    print(f" HEDEFE göre kalan BOŞLUKLAR: {exp.gaps()}")
    print(f" depo: {STORE} ({exp.save_store(STORE)} kabul edilmiş redakte aday)")
    print(f" manifest: {MF} | content_hash: {dv.content_hash}")


if __name__ == "__main__":
    main()
