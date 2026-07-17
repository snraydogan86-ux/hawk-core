"""
Eğitim adayı toplama + zorunlu temizleme hattı + pozitif/negatif örnekler + forget yayılımı.

Ham kullanıcı mesajı HİÇBİR ZAMAN doğrudan eğitim setine girmez. Bir aday ancak 16-adım
hattı + admin onayı geçerse dataset sürümüne alınabilir. Negatif örnekler "ideal düzeltme"
ile ayrı kategoride tutulur (preference/rejection/evaluator eğitimi için).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Optional

# redaction'ı mevcut agent_orchestration katmanından yeniden kullan (tek doğruluk kaynağı)
from core.agent_orchestration.dataset import redact


class Consent(str, Enum):
    UNKNOWN = "unknown"; APPROVED = "approved"; DENIED = "denied"; REVOKED = "revoked"


class CandStatus(str, Enum):
    COLLECTED = "collected"; CLEANED = "cleaned"; ACCEPTED = "accepted"
    REJECTED = "rejected"; REVOKED = "revoked"; DELETED = "deleted"


class Polarity(str, Enum):
    POSITIVE = "positive"          # SFT (test-geçen kod, doğru cevap/tool/plan)
    NEGATIVE = "negative"          # preference/rejection (hallucination, sahte "bitti", güvenlik ihlali)


@dataclass
class TrainingCandidate:
    candidate_id: str
    source_task_id: str = ""
    source_agent_run_id: str = ""
    source_type: str = ""             # multi_agent|workspace|tool|web|memory|forget|security|json|nl
    role: str = ""                    # hawk_base|hawk_code|...
    polarity: Polarity = Polarity.POSITIVE
    input_redacted: str = ""
    output_redacted: str = ""
    tool_trace_redacted: str = ""
    tools_json: str = ""              # v0.8: araç örneğinde sunulan fonksiyon tanımları (JSON string)
    history_json: str = ""            # v0.9: çok-turlu bağlam (önceki user/assistant turları, JSON string)
    ideal_correction: str = ""        # negatif örnek için "olması gereken"
    evidence_refs: tuple[str, ...] = ()
    test_refs: tuple[str, ...] = ()
    reviewer_score: float = 0.0
    factuality_score: float = 0.0
    safety_score: float = 0.0
    quality_score: float = 0.0
    consent_status: Consent = Consent.UNKNOWN
    pii_status: str = "unknown"       # clean|found
    secret_status: str = "unknown"
    license_status: str = "unknown"
    provenance: str = ""
    duplicate_group: str = ""
    accepted: bool = False
    rejection_reason: str = ""
    dataset_target: str = ""          # hawk-dataset-v0.1
    status: CandStatus = CandStatus.COLLECTED

    def content_hash(self) -> str:
        return hashlib.sha256((self.input_redacted + "|" + self.output_redacted).encode()).hexdigest()[:16]


QUALITY_MIN = 0.7
SAFETY_MIN = 0.7

# 16-adım temizleme hattının adım adı listesi (audit/şeffaflık)
CLEAN_STEPS = (
    "consent_check", "pii_detect", "contact_scrub", "identity_scrub", "secret_scrub",
    "credential_scrub", "repo_path_scrub", "private_data_scrub", "injection_scrub",
    "harmful_classify", "license_provenance", "dedup", "quality_score",
    "independent_reviewer", "admin_approval", "accept_into_version",
)


def clean_candidate(c: TrainingCandidate) -> TrainingCandidate:
    """Adım 2-10: redaksiyon + PII/secret tespiti (ham içerik saklanmaz).
    Sonuç redakte alanlar + pii/secret durumu."""
    ir, p1, s1 = redact(c.input_redacted)
    orr, p2, s2 = redact(c.output_redacted)
    tr, p3, s3 = redact(c.tool_trace_redacted)
    c.input_redacted, c.output_redacted, c.tool_trace_redacted = ir, orr, tr
    c.pii_status = "found" if (p1 or p2 or p3) else "clean"
    c.secret_status = "found" if (s1 or s2 or s3) else "clean"
    c.status = CandStatus.CLEANED
    return c


def eligibility(c: TrainingCandidate) -> tuple[bool, list[str]]:
    """Adım 1,11,13,14: dataset sürümüne kabul için tüm kapılar."""
    f = []
    if c.consent_status != Consent.APPROVED:
        f.append("consent_not_approved")
    if c.pii_status != "clean":
        f.append("pii_present")
    if c.secret_status != "clean":
        f.append("secret_present")
    if not c.provenance:
        f.append("provenance_unknown")
    if c.license_status in ("", "unknown", "proprietary", "restricted"):
        f.append("license_not_clear")
    if c.reviewer_score <= 0:
        f.append("no_reviewer_acceptance")
    if c.quality_score < QUALITY_MIN:
        f.append("below_quality")
    if c.safety_score < SAFETY_MIN:
        f.append("below_safety")
    # pozitif örnekte factuality; negatifte ideal_correction zorunlu
    if c.polarity == Polarity.POSITIVE and c.factuality_score < 0.5:
        f.append("low_factuality")
    if c.polarity == Polarity.NEGATIVE and not c.ideal_correction:
        f.append("negative_without_ideal_correction")
    return (len(f) == 0, f)


class CandidatePool:
    def __init__(self):
        self._c: dict[str, TrainingCandidate] = {}
        self._by_hash: dict[str, str] = {}    # content_hash -> candidate_id (dedup)

    def collect(self, c: TrainingCandidate) -> TrainingCandidate:
        clean_candidate(c)
        h = c.content_hash()
        if h in self._by_hash:                # dedup (adım 12)
            c.duplicate_group = self._by_hash[h]
        else:
            self._by_hash[h] = c.candidate_id
            c.duplicate_group = c.candidate_id
        self._c[c.candidate_id] = c
        return c

    def get(self, cid: str) -> TrainingCandidate:
        return self._c[cid]

    def all(self) -> list[TrainingCandidate]:
        return list(self._c.values())

    def accept_into(self, cid: str, dataset_version: str) -> TrainingCandidate:
        c = self._c[cid]
        # duplicate → yalnız grup temsilcisi kabul edilebilir
        if c.duplicate_group != c.candidate_id:
            c.status = CandStatus.REJECTED
            c.rejection_reason = "duplicate"
            return c
        ok, fails = eligibility(c)
        if not ok:
            c.status = CandStatus.REJECTED
            c.rejection_reason = ",".join(fails)
            return c
        c.accepted = True
        c.dataset_target = dataset_version
        c.status = CandStatus.ACCEPTED
        return c

    def positives(self) -> list[TrainingCandidate]:
        return [c for c in self._c.values()
                if c.polarity == Polarity.POSITIVE and c.status == CandStatus.ACCEPTED]

    def negatives(self) -> list[TrainingCandidate]:
        return [c for c in self._c.values() if c.polarity == Polarity.NEGATIVE]

    def counts(self) -> dict:
        acc = sum(1 for c in self._c.values() if c.status == CandStatus.ACCEPTED)
        rej = sum(1 for c in self._c.values() if c.status == CandStatus.REJECTED)
        return {"total": len(self._c), "accepted": acc, "rejected": rej,
                "positive": len(self.positives()), "negative": len(self.negatives())}

    # -- forget/revoke yayılımı (Bölüm 6) --
    def apply_forget(self, *, source_task_id: str = "", source_agent_run_id: str = "",
                     frozen_versions: Optional[set[str]] = None) -> dict:
        """Kullanıcı silme talebi → ilgili adaylar bulunur:
        - accepted DEĞİLse silinir/revoked
        - frozen dataset içindeyse compliance kaydı (ağırlıktan anında silme İDDİA EDİLMEZ)"""
        frozen_versions = frozen_versions or set()
        revoked, deleted, compliance = [], [], []
        for c in self._c.values():
            match = (source_task_id and c.source_task_id == source_task_id) or \
                    (source_agent_run_id and c.source_agent_run_id == source_agent_run_id)
            if not match:
                continue
            if c.status == CandStatus.ACCEPTED and c.dataset_target in frozen_versions:
                c.status = CandStatus.REVOKED
                revoked.append(c.candidate_id)
                compliance.append({
                    "candidate_id": c.candidate_id, "dataset_version": c.dataset_target,
                    "event": "used_in_frozen_dataset",
                    "note": "Gelecek build'lerden çıkarıldı. Model ağırlığından tek-kayıt "
                            "ANINDA silme garanti edilmez.",
                    "requires": "retraining_or_removal_plan"})
            else:
                c.status = CandStatus.DELETED
                deleted.append(c.candidate_id)
        return {"revoked": revoked, "deleted": deleted, "compliance_events": compliance}
