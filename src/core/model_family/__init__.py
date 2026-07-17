"""
HAWK Model Family — sürümlü model ailesi + kontrollü self-improvement altyapısı.

YENİ paket, hiçbir canlı chat/router'a bağlı DEĞİL. GPU açmaz, eğitim başlatmaz,
production ağırlığını değiştirmez. HAWK: veri TOPLAR → TEMİZLER → DEĞERLENDİRİR → ÖNERİR.
Eğitim ve production promotion daima kontrollü, sürümlü, benchmark'lı, geri alınabilir.
"""
from __future__ import annotations

from .model_registry import (
    ModelRegistry, ModelVersion, ModelStatus, RegistryError, CANARY_PCT,
)
from .dataset_registry import (
    DatasetRegistry, DatasetVersion, DatasetStatus, DatasetError,
)
from .candidate import (
    TrainingCandidate, CandidatePool, Consent, CandStatus, Polarity,
    clean_candidate, eligibility, CLEAN_STEPS,
)
from .training_job import TrainingRegistry, TrainingJob, JobStatus, TrainingError
from .evaluation_gates import (
    evaluate_promotion, GateResult, REQUIRED_CATEGORIES, CRITICAL_CATEGORIES,
)
from .shadow_canary import (
    ShadowRecord, CanaryRecord, evaluate_canary, CANARY_THRESHOLDS,
)
from .plans import (
    BASE_PLAN, CODE_PLAN, INDEPENDENCE_PHASES, INDEPENDENT_MUST_WORK, plan_for,
)
from .pipeline import DatasetPipeline, Source
from .seed_candidates import SeedSource, SEED
from .expand import DatasetExpander, FileSource, CATEGORY_TARGETS

__all__ = [
    "DatasetPipeline", "Source", "SeedSource", "SEED",
    "DatasetExpander", "FileSource", "CATEGORY_TARGETS",
    "ModelRegistry", "ModelVersion", "ModelStatus", "RegistryError", "CANARY_PCT",
    "DatasetRegistry", "DatasetVersion", "DatasetStatus", "DatasetError",
    "TrainingCandidate", "CandidatePool", "Consent", "CandStatus", "Polarity",
    "clean_candidate", "eligibility", "CLEAN_STEPS",
    "TrainingRegistry", "TrainingJob", "JobStatus", "TrainingError",
    "evaluate_promotion", "GateResult", "REQUIRED_CATEGORIES", "CRITICAL_CATEGORIES",
    "ShadowRecord", "CanaryRecord", "evaluate_canary", "CANARY_THRESHOLDS",
    "BASE_PLAN", "CODE_PLAN", "INDEPENDENCE_PHASES", "INDEPENDENT_MUST_WORK", "plan_for",
]
