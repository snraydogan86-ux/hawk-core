#!/usr/bin/env python3
"""
hawk-dataset-v0.1'i güvenli SEED verisinden üretir (GPU'suz, production verisi YOK).

Hat: SeedSource → ingest (temizle+dedup) → build_version (eligibility + admin onayı) →
freeze (immutable, hash) → manifest yaz. Reddedilenler (consent/PII/secret/dedup/kalite)
filtrelenir. Manifest ham DEĞER içermez.
"""
from __future__ import annotations

import json
import os
import time

from core.model_family import DatasetPipeline, SeedSource

HERE = os.path.dirname(__file__)
OUT_DIR = os.path.join(HERE, "registry")
OUT = os.path.join(OUT_DIR, "hawk-dataset-v0.1.json")


def main() -> None:
    pipe = DatasetPipeline()
    pipe.ingest(SeedSource())                                  # adım 1-12
    summary = pipe.build_version(                              # adım 13-16 (admin onayı)
        dataset_id="hawk-dataset-v0.1", version="v0.1", admin_approved=True,
        source_categories=("nl", "tool", "workspace", "security", "forget", "json"),
        description="HAWK Base v0.1 kürasyonlu seed — TR/EN sohbet, tool, kod, güvenlik, forget.")
    pipe.freeze("hawk-dataset-v0.1", reviewer_approvals=("soner",), now=time.time())

    dv = pipe.registry.get("hawk-dataset-v0.1")
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(dv.to_dict(), f, ensure_ascii=False, indent=2)

    print("=== hawk-dataset-v0.1 üretildi ===")
    print(f" durum: {dv.status.value} | frozen: {dv.frozen} | integrity: {pipe.registry.verify_integrity(dv.dataset_id)}")
    print(f" kabul: {summary['accepted']} | red: {summary['rejected']}")
    print(f" red nedenleri: {summary['reject_reasons']}")
    print(f" pozitif/negatif: {summary['positives']}/{summary['negatives']}")
    print(f" split: {dv.split} | content_hash: {dv.content_hash} | manifest_hash: {dv.manifest_hash}")
    print(f" manifest: {OUT}")
    # freeze sonrası değişmezlik kanıtı
    from core.model_family import DatasetError
    try:
        pipe.registry.add_records("hawk-dataset-v0.1", accepted=1)
        print(" HATA: frozen dataset değişti (OLMAMALIYDI)")
    except DatasetError:
        print(" freeze SONRASI değişmezlik: mutation reddedildi ✓")


if __name__ == "__main__":
    main()
