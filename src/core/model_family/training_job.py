"""
TrainingJob — eğitim işi şeması ve yaşam döngüsü.

Kritik: training job açık admin onayı OLMADAN başlayamaz. GPU açma ve ücretli servis
AYRI onay gerektirir. hard_cost_limit aşılırsa iş başlamaz/durur. Bu modül YALNIZ
şema+durum; gerçek eğitimi BAŞLATMAZ (GPU yok).
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Optional


class JobStatus(str, Enum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    PENDING_GPU_APPROVAL = "pending_gpu_approval"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class TrainingJob:
    training_job_id: str
    model_target: str                 # hawk-base-v0.1
    base_model: str
    dataset_version: str
    method: str = "lora"              # lora|qlora|full
    lora_config: dict[str, Any] = field(default_factory=dict)
    learning_rate: float = 1e-4
    sequence_length: int = 4096
    batch_size: int = 8
    gradient_accumulation: int = 4
    epochs: int = 1
    seed: int = 42
    hardware: str = ""
    estimated_cost: float = 0.0
    hard_cost_limit: float = 0.0
    max_runtime_s: int = 0
    checkpoint_interval: int = 0
    output_location: str = ""
    status: JobStatus = JobStatus.DRAFT
    logs_ref: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)
    admin_approved_by: str = ""
    gpu_approved_by: str = ""
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    failure_reason: str = ""
    history: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        return d


class TrainingError(Exception):
    pass


class TrainingRegistry:
    def __init__(self):
        self._jobs: dict[str, TrainingJob] = {}

    def create(self, job: TrainingJob) -> TrainingJob:
        if job.training_job_id in self._jobs:
            raise TrainingError("job_id zaten var")
        if not job.dataset_version:
            raise TrainingError("dataset_version zorunlu (frozen sürüm)")
        if job.hard_cost_limit <= 0:
            raise TrainingError("hard_cost_limit zorunlu ( > 0 )")
        job.status = JobStatus.PENDING_APPROVAL
        job.history.append("created:pending_approval")
        self._jobs[job.training_job_id] = job
        return job

    def get(self, jid: str) -> TrainingJob:
        return self._jobs[jid]

    def approve(self, jid: str, *, admin: str) -> TrainingJob:
        j = self._jobs[jid]
        if j.status != JobStatus.PENDING_APPROVAL:
            raise TrainingError("yalnız pending_approval onaylanabilir")
        if not admin:
            raise TrainingError("admin kimliği zorunlu")
        j.admin_approved_by = admin
        j.status = JobStatus.PENDING_GPU_APPROVAL     # eğitim onaylandı, GPU AYRI onay
        j.history.append(f"approved_by:{admin}")
        return j

    def approve_gpu(self, jid: str, *, admin: str) -> TrainingJob:
        """GPU açma/ücretli servis AYRI onay (Bölüm 7)."""
        j = self._jobs[jid]
        if j.status != JobStatus.PENDING_GPU_APPROVAL:
            raise TrainingError("önce eğitim onayı gerekir")
        if not admin:
            raise TrainingError("admin kimliği zorunlu")
        j.gpu_approved_by = admin
        j.status = JobStatus.READY
        j.history.append(f"gpu_approved_by:{admin}")
        return j

    def start(self, jid: str, *, now: float = 0.0) -> TrainingJob:
        """Yalnız READY (iki onay + bütçe) işi başlatılabilir. Bu modül gerçek eğitim
        BAŞLATMAZ — yalnız durum geçişi (GPU harici onaylı runner tarafından çağrılır)."""
        j = self._jobs[jid]
        if j.status != JobStatus.READY:
            raise TrainingError("iş READY değil (admin+GPU onayı eksik)")
        if j.estimated_cost > j.hard_cost_limit:
            j.status = JobStatus.FAILED
            j.failure_reason = "estimated_cost > hard_cost_limit"
            raise TrainingError("bütçe tavanı aşılıyor — iş başlatılmaz")
        j.status = JobStatus.RUNNING
        j.started_at = now
        j.history.append("started")
        return j

    def finish(self, jid: str, *, ok: bool, now: float = 0.0,
               metrics: Optional[dict] = None, reason: str = "") -> TrainingJob:
        j = self._jobs[jid]
        if j.status != JobStatus.RUNNING:
            raise TrainingError("yalnız running iş bitirilebilir")
        j.status = JobStatus.COMPLETED if ok else JobStatus.FAILED
        j.completed_at = now
        if metrics:
            j.metrics = metrics
        if not ok:
            j.failure_reason = reason
        j.history.append("completed" if ok else f"failed:{reason}")
        return j

    def cancel(self, jid: str) -> TrainingJob:
        j = self._jobs[jid]
        if j.status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
            raise TrainingError("terminal iş iptal edilemez")
        j.status = JobStatus.CANCELLED
        j.history.append("cancelled")
        return j
