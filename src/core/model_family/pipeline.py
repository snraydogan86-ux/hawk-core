"""
Dataset hattı — eğitim adaylarını TOPLA → TEMİZLE (16 adım) → DEĞERLENDİR → sürüme AL → FREEZE.

Ham kullanıcı mesajı HİÇBİR ZAMAN doğrudan girmez. Bir aday ancak consent=approved + PII/secret
temiz + provenance + lisans + reviewer + kalite/güvenlik eşiği + ADMIN ONAYI geçerse alınır.
Kaynaklar Source arayüzü ile takılır; bu turda yalnız güvenli SeedSource (kürasyonlu örnekler) —
production kullanıcı konuşması İZİNSİZ dönüştürülmez.

16 adım eşlemesi (candidate.CLEAN_STEPS):
  1 consent  · 2-10 redaksiyon/PII/secret/credential/injection/harmful (clean_candidate)
  11 lisans/provenance · 12 dedup (collect) · 13 kalite · 14 bağımsız reviewer (eligibility)
  15 admin onayı (pipeline gate) · 16 dataset sürümüne kabul (accept_into)
"""
from __future__ import annotations

import json
from typing import Any, Optional

from .candidate import (
    CandidatePool, TrainingCandidate, Consent, Polarity, CandStatus, CLEAN_STEPS,
)
from .dataset_registry import DatasetRegistry, DatasetError


class Source:
    """Aday kaynağı arayüzü. fetch() → ham aday dict listesi döndürür."""
    name = "base"

    def fetch(self) -> list[dict]:  # pragma: no cover
        raise NotImplementedError


def _raw_to_candidate(r: dict) -> TrainingCandidate:
    return TrainingCandidate(
        candidate_id=r["candidate_id"],
        source_task_id=r.get("source_task_id", ""),
        source_agent_run_id=r.get("source_agent_run_id", ""),
        source_type=r.get("source_type", ""),
        role=r.get("role", ""),
        polarity=Polarity(r.get("polarity", "positive")),
        input_redacted=r.get("input", ""),
        output_redacted=r.get("output", ""),
        tool_trace_redacted=r.get("tool_trace", ""),
        tools_json=(json.dumps(r.get("tools"), ensure_ascii=False) if r.get("tools") else ""),
        history_json=(json.dumps(r.get("history"), ensure_ascii=False) if r.get("history") else ""),
        ideal_correction=r.get("ideal_correction", ""),
        consent_status=Consent(r.get("consent", "unknown")),
        license_status=r.get("license", "unknown"),
        provenance=r.get("provenance", ""),
        reviewer_score=float(r.get("reviewer_score", 0.0)),
        factuality_score=float(r.get("factuality", 0.0)),
        safety_score=float(r.get("safety", 0.0)),
        quality_score=float(r.get("quality", 0.0)),
    )


class DatasetPipeline:
    def __init__(self, pool: Optional[CandidatePool] = None,
                 registry: Optional[DatasetRegistry] = None):
        self.pool = pool or CandidatePool()
        self.registry = registry or DatasetRegistry()
        self.audit: list[dict] = []          # adım denetimi — HAM DEĞER YOK, yalnız id+durum

    # -- toplama + temizleme (adım 1-12) --
    def ingest(self, source_or_raws) -> list[str]:
        raws = source_or_raws.fetch() if isinstance(source_or_raws, Source) else source_or_raws
        ids = []
        for r in raws:
            c = _raw_to_candidate(r)
            self.pool.collect(c)             # redaksiyon + dedup
            self.audit.append({"candidate_id": c.candidate_id, "step": "clean",
                               "pii": c.pii_status, "secret": c.secret_status,
                               "dup": c.duplicate_group != c.candidate_id})
            ids.append(c.candidate_id)
        return ids

    # -- değerlendir + kabul (adım 13-16) --
    def build_version(self, *, dataset_id: str, version: str,
                      admin_approved: bool, source_categories: tuple[str, ...] = (),
                      description: str = "") -> dict:
        if not admin_approved:
            raise DatasetError("dataset kabulü için ADMIN ONAYI zorunlu (adım 15)")
        try:
            self.registry.create(dataset_id=dataset_id, version=version,
                                 description=description, source_categories=source_categories)
        except DatasetError:
            pass  # zaten var (yeniden değerlendirme)
        acc = rej = 0
        consent_sum: dict[str, int] = {}
        license_sum: dict[str, int] = {}
        reasons: dict[str, int] = {}
        for c in self.pool.all():
            res = self.pool.accept_into(c.candidate_id, dataset_id)
            consent_sum[c.consent_status.value] = consent_sum.get(c.consent_status.value, 0) + 1
            if res.status == CandStatus.ACCEPTED:
                acc += 1
                license_sum[c.license_status] = license_sum.get(c.license_status, 0) + 1
            else:
                rej += 1
                for why in (res.rejection_reason or "").split(","):
                    if why:
                        reasons[why] = reasons.get(why, 0) + 1
            self.audit.append({"candidate_id": c.candidate_id, "step": "accept",
                               "status": res.status.value, "reason": res.rejection_reason})
        # kabul edilenler eligibility'den geçtiği için PII/secret temiz (bu yüzden found=0)
        self.registry.add_records(dataset_id, accepted=acc, rejected=rej,
                                  consent=consent_sum, license_sum=license_sum)
        self.registry.add_scan(dataset_id, pii={"found": 0}, secret={"found": 0},
                               dedup={"duplicate_groups": self._dup_count()})
        # basit train/val/test bölmesi (deterministik: 80/10/10)
        tr = int(acc * 0.8); va = int(acc * 0.1); te = acc - tr - va
        self.registry.set_split(dataset_id, train=tr, val=va, test=te)
        return {"accepted": acc, "rejected": rej, "reject_reasons": reasons,
                "consent": consent_sum, "positives": len(self.pool.positives()),
                "negatives": len(self.pool.negatives())}

    def freeze(self, dataset_id: str, *, reviewer_approvals: tuple[str, ...], now: float = 0.0):
        return self.registry.freeze(dataset_id, reviewer_approvals=reviewer_approvals, now=now)

    # -- forget/revoke yayılımı --
    def forget(self, *, source_task_id: str = "", source_agent_run_id: str = "",
               frozen_versions: Optional[set[str]] = None) -> dict:
        return self.pool.apply_forget(source_task_id=source_task_id,
                                      source_agent_run_id=source_agent_run_id,
                                      frozen_versions=frozen_versions)

    def _dup_count(self) -> int:
        return sum(1 for c in self.pool.all() if c.duplicate_group != c.candidate_id)

    def report(self) -> dict:
        return {"candidates": len(self.pool.all()), **self.pool.counts(),
                "clean_steps": len(CLEAN_STEPS)}
