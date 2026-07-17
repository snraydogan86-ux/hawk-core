"""
Dataset Versioning — sürümlü, freeze edilebilir eğitim veri seti kaydı.

Kurallar:
  - Dataset FREEZE edildikten sonra içeriği SESSİZCE değiştirilemez.
  - Yeni veri gerekirse YENİ sürüm oluşturulur.
  - content_hash + manifest_hash freeze anında sabitlenir; sonradan uyuşmazlık tespit edilir.
Saf veri (I/O yok) → deterministik.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Optional


class DatasetStatus(str, Enum):
    DRAFT = "draft"
    REVIEW = "review"
    FROZEN = "frozen"
    RETIRED = "retired"


@dataclass
class DatasetVersion:
    dataset_id: str
    version: str                        # v0.1|v0.2|v1.0
    description: str = ""
    source_categories: tuple[str, ...] = ()
    accepted_count: int = 0
    rejected_count: int = 0
    consent_summary: dict[str, int] = field(default_factory=dict)
    pii_scan: dict[str, Any] = field(default_factory=dict)
    secret_scan: dict[str, Any] = field(default_factory=dict)
    license_summary: dict[str, int] = field(default_factory=dict)
    dedup_result: dict[str, Any] = field(default_factory=dict)
    split: dict[str, int] = field(default_factory=dict)   # train/val/test
    content_hash: str = ""
    manifest_hash: str = ""
    reviewer_approvals: tuple[str, ...] = ()
    created_at: float = 0.0
    frozen_at: Optional[float] = None
    status: DatasetStatus = DatasetStatus.DRAFT

    @property
    def frozen(self) -> bool:
        return self.status == DatasetStatus.FROZEN

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        return d


class DatasetError(Exception):
    pass


def _hash(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


class DatasetRegistry:
    def __init__(self):
        self._ds: dict[str, DatasetVersion] = {}

    def create(self, *, dataset_id: str, version: str, description: str = "",
               source_categories: tuple[str, ...] = ()) -> DatasetVersion:
        if dataset_id in self._ds:
            raise DatasetError(f"dataset_id zaten var: {dataset_id}")
        dv = DatasetVersion(dataset_id=dataset_id, version=version,
                            description=description, source_categories=source_categories)
        self._ds[dataset_id] = dv
        return dv

    def get(self, dataset_id: str) -> DatasetVersion:
        return self._ds[dataset_id]

    def add_records(self, dataset_id: str, *, accepted: int = 0, rejected: int = 0,
                    consent: Optional[dict] = None, license_sum: Optional[dict] = None) -> None:
        dv = self._ds[dataset_id]
        if dv.frozen:
            raise DatasetError("FROZEN dataset değiştirilemez — yeni sürüm oluştur")
        dv.accepted_count += accepted
        dv.rejected_count += rejected
        for k, v in (consent or {}).items():
            dv.consent_summary[k] = dv.consent_summary.get(k, 0) + v
        for k, v in (license_sum or {}).items():
            dv.license_summary[k] = dv.license_summary.get(k, 0) + v

    def set_split(self, dataset_id: str, *, train: int, val: int, test: int) -> None:
        dv = self._ds[dataset_id]
        if dv.frozen:
            raise DatasetError("FROZEN dataset değiştirilemez")
        dv.split = {"train": train, "val": val, "test": test}

    def add_scan(self, dataset_id: str, *, pii: Optional[dict] = None,
                 secret: Optional[dict] = None, dedup: Optional[dict] = None) -> None:
        dv = self._ds[dataset_id]
        if dv.frozen:
            raise DatasetError("FROZEN dataset değiştirilemez")
        if pii is not None:
            dv.pii_scan = pii
        if secret is not None:
            dv.secret_scan = secret
        if dedup is not None:
            dv.dedup_result = dedup

    def freeze(self, dataset_id: str, *, reviewer_approvals: tuple[str, ...],
               now: float = 0.0) -> DatasetVersion:
        """Freeze: içerik hash'i sabitlenir; reviewer onayı zorunlu; PII/secret temiz olmalı."""
        dv = self._ds[dataset_id]
        if dv.frozen:
            raise DatasetError("zaten frozen")
        if not reviewer_approvals:
            raise DatasetError("freeze için en az bir reviewer onayı zorunlu")
        if dv.secret_scan.get("found", 0):
            raise DatasetError("secret bulunan dataset freeze edilemez")
        if dv.pii_scan.get("found", 0):
            raise DatasetError("PII bulunan dataset freeze edilemez")
        if dv.accepted_count <= 0:
            raise DatasetError("kabul edilmiş aday yok")
        dv.reviewer_approvals = reviewer_approvals
        dv.content_hash = _hash({
            "accepted": dv.accepted_count, "split": dv.split,
            "consent": dv.consent_summary, "license": dv.license_summary})
        dv.manifest_hash = _hash(dv.to_dict())
        dv.frozen_at = now
        dv.status = DatasetStatus.FROZEN
        return dv

    def verify_integrity(self, dataset_id: str) -> bool:
        """Freeze sonrası içerik sessizce değişmiş mi? (content_hash tekrar hesabı)."""
        dv = self._ds[dataset_id]
        if not dv.frozen:
            return True
        expect = _hash({
            "accepted": dv.accepted_count, "split": dv.split,
            "consent": dv.consent_summary, "license": dv.license_summary})
        return expect == dv.content_hash
