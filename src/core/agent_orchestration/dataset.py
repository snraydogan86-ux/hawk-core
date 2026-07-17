"""
DatasetCandidate + consent/redaction (Section 16-17).

Kontrollü model gelişimi: production konuşmaları VARSAYILAN olarak eğitim verisi
DEĞİLDİR. Bir örnek training dataset'e ancak TÜM kapılar geçilirse alınır:
  consent=approved · PII temiz · secret temiz · provenance biliniyor · lisans uygun
  · reviewer kabulü · quality eşiği · security skoru.

forget/delete talebi ilgili adayı revoked/deleted işaretler; gelecekteki build'e
dahil edilmez. Kullanılmış bir dataset sürümündeyse compliance/audit olayı doğurur.
Model ağırlığından "tek kaydı anında sildim" İDDİA EDİLMEZ — retraining/removal süreci
dürüstçe raporlanır.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class ConsentStatus(str, Enum):
    UNKNOWN = "unknown"
    APPROVED = "approved"
    DENIED = "denied"
    REVOKED = "revoked"


class CandidateStatus(str, Enum):
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    REVOKED = "revoked"
    DELETED = "deleted"


_PII = [
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),                 # email
    re.compile(r"\+?\d[\d\s().-]{8,}\d"),                    # phone
    re.compile(r"\bTR\d{2}[\dA-Z ]{16,}\b", re.I),          # IBAN-ish
    re.compile(r"\b\d{11}\b"),                               # TC-kimlik uzunluğu
]
_SECRET = [
    re.compile(r"sk-[A-Za-z0-9\-_]{8,}"),
    re.compile(r"AKIA[0-9A-Z]{12,}"),
    re.compile(r"-----BEGIN [A-Z ]+PRIVATE KEY-----"),
    re.compile(r"(?i)(api[_-]?key|password|secret|token)\s*[=:]\s*\S+"),
]


def redact(text: str) -> tuple[str, bool, bool]:
    """(redacted, pii_found, secret_found)."""
    pii = secret = False
    out = text or ""
    for rx in _SECRET:
        if rx.search(out):
            secret = True
            out = rx.sub("[SECRET]", out)
    for rx in _PII:
        if rx.search(out):
            pii = True
            out = rx.sub("[PII]", out)
    return out, pii, secret


@dataclass
class DatasetCandidate:
    candidate_id: str
    source_type: str                      # agent_execution|correction|...
    source_execution_id: str
    consent_status: ConsentStatus = ConsentStatus.UNKNOWN
    pii_detected: bool = False
    secret_detected: bool = False
    license_status: str = "unknown"       # apache-2.0|proprietary|unknown|...
    provenance: str = ""
    input_redacted: str = ""
    output_redacted: str = ""
    tool_trace_redacted: str = ""
    quality_score: float = 0.0
    reviewer_score: float = 0.0
    security_score: float = 0.0
    status: CandidateStatus = CandidateStatus.PROPOSED
    rejection_reason: str = ""
    dataset_version: str = ""
    deletion_status: str = ""

    def content_hash(self) -> str:
        return hashlib.sha256((self.input_redacted + "|" + self.output_redacted).encode()).hexdigest()[:16]


def build_candidate(*, candidate_id: str, source_execution_id: str,
                    raw_input: str, raw_output: str, tool_trace: str = "",
                    consent: ConsentStatus = ConsentStatus.UNKNOWN,
                    license_status: str = "unknown", provenance: str = "",
                    quality_score: float = 0.0, reviewer_score: float = 0.0,
                    security_score: float = 0.0,
                    source_type: str = "agent_execution") -> DatasetCandidate:
    ir, p1, s1 = redact(raw_input)
    orr, p2, s2 = redact(raw_output)
    tr, p3, s3 = redact(tool_trace)
    return DatasetCandidate(
        candidate_id=candidate_id, source_type=source_type,
        source_execution_id=source_execution_id, consent_status=consent,
        pii_detected=p1 or p2 or p3, secret_detected=s1 or s2 or s3,
        license_status=license_status, provenance=provenance,
        input_redacted=ir, output_redacted=orr, tool_trace_redacted=tr,
        quality_score=quality_score, reviewer_score=reviewer_score,
        security_score=security_score,
    )


# eğitim setine kabul için EŞ ZAMANLI geçilmesi gereken tüm kapılar
QUALITY_THRESHOLD = 0.7
SECURITY_THRESHOLD = 0.7


def eligibility(c: DatasetCandidate) -> tuple[bool, list[str]]:
    fails = []
    if c.consent_status != ConsentStatus.APPROVED:
        fails.append("consent_not_approved")
    if c.pii_detected:
        fails.append("pii_detected")
    if c.secret_detected:
        fails.append("secret_detected")
    if not c.provenance:
        fails.append("provenance_unknown")
    if c.license_status in ("", "unknown", "proprietary", "restricted"):
        fails.append("license_not_clear")
    if c.reviewer_score <= 0:
        fails.append("no_reviewer_acceptance")
    if c.quality_score < QUALITY_THRESHOLD:
        fails.append("below_quality_threshold")
    if c.security_score < SECURITY_THRESHOLD:
        fails.append("below_security_threshold")
    return (len(fails) == 0, fails)


def accept_into_version(c: DatasetCandidate, version: str) -> DatasetCandidate:
    ok, fails = eligibility(c)
    if not ok:
        c.status = CandidateStatus.REJECTED
        c.rejection_reason = ",".join(fails)
        return c
    c.status = CandidateStatus.ACCEPTED
    c.dataset_version = version
    return c


def apply_forget(candidates: list[DatasetCandidate], source_execution_id: str) -> dict:
    """forget/delete → ilgili adayları revoked/deleted işaretle. Kullanılmış sürümde
    ise dürüst compliance olayı üret (model ağırlığından anında silme İDDİA EDİLMEZ)."""
    revoked, compliance = [], []
    for c in candidates:
        if c.source_execution_id != source_execution_id:
            continue
        if c.status == CandidateStatus.ACCEPTED and c.dataset_version:
            c.status = CandidateStatus.REVOKED
            c.deletion_status = "revoked_from_future_builds"
            compliance.append({
                "candidate_id": c.candidate_id, "dataset_version": c.dataset_version,
                "event": "used_in_dataset_version",
                "note": "Gelecek build'lerden çıkarıldı. Mevcut model ağırlığından "
                        "tek-kayıt anında silme GARANTİ EDİLMEZ; retraining/removal süreci gerekir.",
                "requires": "retraining_or_removal_process",
            })
            revoked.append(c.candidate_id)
        else:
            c.status = CandidateStatus.DELETED
            c.deletion_status = "deleted"
            revoked.append(c.candidate_id)
    return {"revoked": revoked, "compliance_events": compliance}
