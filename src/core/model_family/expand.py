"""
Dataset genişletme aracı (GPU'suz).

Mevcut hattın üstüne: (a) dosyadan toplu aday ekleme (FileSource/JSONL), (b) sürümler
arası KALICI aday deposu (redakte edilmiş — güvenli), (c) kapsama/denge raporu +
hedefe göre BOŞLUK analizi, (d) yeni sürüm (v0.2, v0.3...) üretimi (frozen v0.1'e dokunmadan).

Ham kullanıcı mesajı girmez; her aday yine consent+PII/secret+provenance+lisans+kalite+admin
kapılarından geçer. Depoya YALNIZ kabul edilmiş (redakte) adaylar yazılır.
"""
from __future__ import annotations

import json
import os
from typing import Optional

from .pipeline import DatasetPipeline, Source
from .candidate import CandStatus, Polarity, TrainingCandidate
from .dataset_registry import DatasetRegistry

# gerçek-kullanışlı bir dataset için kategori bazında hedef minimumlar (yol gösterici)
CATEGORY_TARGETS = {
    "nl": 60, "tool": 25, "workspace": 30, "code": 40, "security": 20,
    "forget": 12, "json": 20, "reasoning": 25, "memory": 15,
}


class FileSource(Source):
    """JSONL dosyasından ham aday okur (# veya // ile başlayan satırlar yorum)."""
    def __init__(self, path: str):
        self.path = path
        self.name = f"file:{os.path.basename(path)}"

    def fetch(self) -> list[dict]:
        out = []
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if s and not s.startswith(("#", "//")):
                    out.append(json.loads(s))
        return out


def _candidate_to_store(c: TrainingCandidate) -> dict:
    """Redakte adayı depo satırına çevir (ham secret/PII zaten temizlenmiş)."""
    return {"candidate_id": c.candidate_id, "source_type": c.source_type, "role": c.role,
            "polarity": c.polarity.value, "input": c.input_redacted, "output": c.output_redacted,
            "tool_trace": c.tool_trace_redacted, "ideal_correction": c.ideal_correction,
            "tools": (json.loads(c.tools_json) if getattr(c, "tools_json", "") else []),
            "history": (json.loads(c.history_json) if getattr(c, "history_json", "") else []),
            "consent": c.consent_status.value, "license": c.license_status,
            "provenance": c.provenance, "reviewer_score": c.reviewer_score,
            "factuality": c.factuality_score, "safety": c.safety_score, "quality": c.quality_score}


class DatasetExpander:
    def __init__(self, registry: Optional[DatasetRegistry] = None):
        self.pipe = DatasetPipeline(registry=registry)
        self.summary: dict = {}

    # -- kaynak ekleme --
    def load_store(self, path: str) -> "DatasetExpander":
        """Önceki sürümün kabul-deposunu (JSONL) yükle (sürümler arası birikim)."""
        if os.path.exists(path):
            self.pipe.ingest(FileSource(path))
        return self

    def add(self, source_or_raws) -> "DatasetExpander":
        self.pipe.ingest(source_or_raws)
        return self

    # -- kabul edilenler --
    def accepted(self) -> list[TrainingCandidate]:
        return [c for c in self.pipe.pool.all() if c.status == CandStatus.ACCEPTED]

    # -- rapor --
    def coverage(self) -> dict[str, int]:
        cov: dict[str, int] = {}
        for c in self.accepted():
            cov[c.source_type] = cov.get(c.source_type, 0) + 1
        return cov

    def gaps(self, targets: Optional[dict] = None) -> dict[str, int]:
        targets = targets or CATEGORY_TARGETS
        cov = self.coverage()
        return {k: targets[k] - cov.get(k, 0) for k in targets if cov.get(k, 0) < targets[k]}

    def balance(self) -> dict:
        acc = self.accepted()
        by_role: dict[str, int] = {}
        for c in acc:
            by_role[c.role] = by_role.get(c.role, 0) + 1
        return {
            "accepted": len(acc),
            "positive": sum(1 for c in acc if c.polarity == Polarity.POSITIVE),
            "negative": sum(1 for c in acc if c.polarity == Polarity.NEGATIVE),
            "by_category": self.coverage(),
            "by_role": by_role,
        }

    # -- sürüm üret + kalıcılaştır --
    def build(self, *, dataset_id: str, version: str, admin_approved: bool,
              reviewer_approvals: tuple[str, ...], store_path: Optional[str] = None,
              source_categories: tuple[str, ...] = (), description: str = "",
              now: float = 0.0) -> dict:
        self.summary = self.pipe.build_version(
            dataset_id=dataset_id, version=version, admin_approved=admin_approved,
            source_categories=source_categories, description=description)
        self.pipe.freeze(dataset_id, reviewer_approvals=reviewer_approvals, now=now)
        if store_path:
            self.save_store(store_path)
        return self.summary

    def save_store(self, path: str) -> int:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        acc = self.accepted()
        with open(path, "w", encoding="utf-8") as f:
            for c in acc:
                f.write(json.dumps(_candidate_to_store(c), ensure_ascii=False) + "\n")
        return len(acc)
