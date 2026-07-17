#!/usr/bin/env python3
"""hawk-dataset-v1.0 = seed + v0.2..v0.9 + v1.0. v1.0 = injection-direnç + forget (v0.6 açık fix). GPU'suz."""
from __future__ import annotations
import json, os, time
from core.model_family import SeedSource
from core.model_family.expand import DatasetExpander, FileSource
HERE = os.path.dirname(__file__); REG = os.path.join(HERE, "registry")
ADDS = [os.path.join(REG, "candidates", f"additions_v0.{v}.jsonl") for v in (2, 3, 4, 5, 6, 7, 8, 9)]
ADDS.append(os.path.join(REG, "candidates", "additions_v1.0.jsonl"))
STORE = os.path.join(REG, "candidates", "hawk-dataset-v1.0.jsonl"); MF = os.path.join(REG, "hawk-dataset-v1.0.json")
def main():
    exp = DatasetExpander(); exp.add(SeedSource())
    for p in ADDS: exp.add(FileSource(p))
    s = exp.build(dataset_id="hawk-dataset-v1.0", version="v1.0", admin_approved=True, reviewer_approvals=("soner",),
        store_path=STORE, now=time.time(),
        source_categories=("nl", "tool", "workspace", "code", "security", "forget", "json", "reasoning", "memory"),
        description="HAWK Base v1.0 — v0.9 + injection-direnç + forget (v0.6 prompt_injection 0.60 / forget 0.67 fix).")
    dv = exp.pipe.registry.get("hawk-dataset-v1.0"); json.dump(dv.to_dict(), open(MF, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("kabul:", s['accepted'], "| red:", s['rejected'])
    print("denge:", exp.balance())
if __name__ == "__main__": main()
