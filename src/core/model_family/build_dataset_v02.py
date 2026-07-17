#!/usr/bin/env python3
"""
hawk-dataset-v0.2 üretir = v0.1 seed + kürasyonlu EKLEMELER (GPU'suz, production verisi YOK).
frozen v0.1'e DOKUNMAZ (yeni sürüm). Kabul-deposunu JSONL'e yazar (v0.3 için birikim).
Kapsama + boşluk raporu üretir (gerçek fine-tune için hangi kategoriler eksik).
"""
from __future__ import annotations

import json
import os
import time

from core.model_family import SeedSource
from core.model_family.expand import DatasetExpander, FileSource

HERE = os.path.dirname(__file__)
REG = os.path.join(HERE, "registry")
ADD = os.path.join(REG, "candidates", "additions_v0.2.jsonl")
STORE = os.path.join(REG, "candidates", "hawk-dataset-v0.2.jsonl")
MF = os.path.join(REG, "hawk-dataset-v0.2.json")


def main() -> None:
    exp = DatasetExpander()
    exp.add(SeedSource())               # v0.1 temeli
    exp.add(FileSource(ADD))            # v0.2 eklemeleri
    summary = exp.build(
        dataset_id="hawk-dataset-v0.2", version="v0.2", admin_approved=True,
        reviewer_approvals=("soner",), store_path=STORE, now=time.time(),
        source_categories=("nl", "tool", "workspace", "security", "forget", "json",
                           "reasoning", "memory"),
        description="HAWK Base v0.2 — v0.1 seed + reasoning/memory/workspace eklemeleri.")
    dv = exp.pipe.registry.get("hawk-dataset-v0.2")
    json.dump(dv.to_dict(), open(MF, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    print("=== hawk-dataset-v0.2 üretildi (frozen) ===")
    print(f" kabul: {summary['accepted']} | red: {summary['rejected']} | red nedenleri: {summary['reject_reasons']}")
    print(f" denge: {exp.balance()}")
    print(f" kapsama: {exp.coverage()}")
    print(f" HEDEFE göre BOŞLUKLAR (daha fazla örnek gerek): {exp.gaps()}")
    print(f" depo: {STORE} ({exp.save_store(STORE)} kabul edilmiş redakte aday)")
    print(f" manifest: {MF} | content_hash: {dv.content_hash}")
    print(" NOT: v0.1 frozen manifesti değişmedi (yeni sürüm).")


if __name__ == "__main__":
    main()
