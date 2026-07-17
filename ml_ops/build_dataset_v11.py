#!/usr/bin/env python3
"""hawk-dataset-v1.1 (v0.8 modeli) = seed + v0.2..v0.9 + v1.0 + v1.1. PI-03 injection + FG-02 forget güçlendirme. GPU'suz."""
from __future__ import annotations
import json, os, time
from core.model_family import SeedSource
from core.model_family.expand import DatasetExpander, FileSource
HERE = os.path.dirname(__file__); REG = os.path.join(HERE, "registry")
ADDS = [os.path.join(REG, "candidates", f"additions_v0.{v}.jsonl") for v in (2, 3, 4, 5, 6, 7, 8, 9)]
ADDS += [os.path.join(REG, "candidates", "additions_v1.0.jsonl"), os.path.join(REG, "candidates", "additions_v1.1.jsonl")]
STORE = os.path.join(REG, "candidates", "hawk-dataset-v1.1.jsonl"); MF = os.path.join(REG, "hawk-dataset-v1.1.json")
def main():
    exp = DatasetExpander(); exp.add(SeedSource())
    for p in ADDS: exp.add(FileSource(p))
    s = exp.build(dataset_id="hawk-dataset-v1.1", version="v1.1", admin_approved=True, reviewer_approvals=("soner",),
        store_path=STORE, now=time.time(),
        source_categories=("nl", "tool", "workspace", "code", "security", "forget", "json", "reasoning", "memory"),
        description="HAWK Base v0.8 (dataset v1.1) — v1.0 + PI-03 injection-güçlendirme + FG-02 forget-güçlendirme.")
    dv = exp.pipe.registry.get("hawk-dataset-v1.1"); json.dump(dv.to_dict(), open(MF, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("kabul:", s['accepted'], "| red:", s['rejected'])
    print("denge:", exp.balance())
if __name__ == "__main__": main()
