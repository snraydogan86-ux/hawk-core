#!/usr/bin/env python3
"""hawk-dataset-v0.6 = seed + v0.2..v0.6 eklemeler. 16-adim governance. GPU'suz."""
from __future__ import annotations
import json, os, time
from core.model_family import SeedSource
from core.model_family.expand import DatasetExpander, FileSource
HERE=os.path.dirname(__file__); REG=os.path.join(HERE,"registry")
ADDS=[os.path.join(REG,"candidates",f"additions_v0.{v}.jsonl") for v in (2,3,4,5,6)]
STORE=os.path.join(REG,"candidates","hawk-dataset-v0.6.jsonl"); MF=os.path.join(REG,"hawk-dataset-v0.6.json")
def main():
    exp=DatasetExpander(); exp.add(SeedSource())
    for p in ADDS: exp.add(FileSource(p))
    s=exp.build(dataset_id="hawk-dataset-v0.6",version="v0.6",admin_approved=True,reviewer_approvals=("soner",),
        store_path=STORE,now=time.time(),
        source_categories=("nl","tool","workspace","code","security","forget","json","reasoning","memory"),
        description="HAWK Base v0.6 — reddedis+1.sahis kimlik guclendirme (v0.2 eval fix).")
    dv=exp.pipe.registry.get("hawk-dataset-v0.6"); json.dump(dv.to_dict(),open(MF,"w",encoding="utf-8"),ensure_ascii=False,indent=2)
    print("kabul:",s['accepted'],"| red:",s['rejected'],s['reject_reasons'])
    print("denge:",exp.balance())
    print("depo:",exp.save_store(STORE),"aday")
if __name__=="__main__": main()
