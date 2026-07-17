"""
HAWK Model Registry — sürümlü model ailesi (hawk-base/code/reasoning/mini/vision).

Kurallar:
  - Model production'da kendi ağırlığını OTOMATİK değiştirmez.
  - Sıra atlayarak production'a geçilemez (izinli-geçiş grafiği).
  - İleri geçişler açık admin onayı + geçen gate ister.
  - Rollback admin onayıyla veya kritik güvenlikte fail-safe.
Saf veri + durum makinesi (I/O yok) → deterministik test.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Optional


class ModelStatus(str, Enum):
    DRAFT = "draft"
    DATASET_PREPARING = "dataset_preparing"
    APPROVED_FOR_TRAINING = "approved_for_training"
    TRAINING = "training"
    TRAINING_FAILED = "training_failed"
    TRAINED = "trained"
    OFFLINE_EVAL = "offline_eval"
    SECURITY_EVAL = "security_eval"
    REJECTED = "rejected"
    SHADOW = "shadow"
    CANARY_1 = "canary_1"
    CANARY_5 = "canary_5"
    CANARY_20 = "canary_20"
    CANARY_50 = "canary_50"
    PRODUCTION = "production"
    ROLLED_BACK = "rolled_back"
    RETIRED = "retired"


S = ModelStatus
# izinli ileri/yan geçişler (sıra atlanamaz)
_TRANSITIONS: dict[ModelStatus, set[ModelStatus]] = {
    S.DRAFT: {S.DATASET_PREPARING, S.REJECTED},
    S.DATASET_PREPARING: {S.APPROVED_FOR_TRAINING, S.REJECTED},
    S.APPROVED_FOR_TRAINING: {S.TRAINING, S.REJECTED},
    S.TRAINING: {S.TRAINED, S.TRAINING_FAILED},
    S.TRAINING_FAILED: {S.APPROVED_FOR_TRAINING, S.REJECTED},
    S.TRAINED: {S.OFFLINE_EVAL, S.REJECTED},
    S.OFFLINE_EVAL: {S.SECURITY_EVAL, S.REJECTED},
    S.SECURITY_EVAL: {S.SHADOW, S.REJECTED},
    S.SHADOW: {S.CANARY_1, S.REJECTED},
    S.CANARY_1: {S.CANARY_5, S.ROLLED_BACK},
    S.CANARY_5: {S.CANARY_20, S.ROLLED_BACK},
    S.CANARY_20: {S.CANARY_50, S.ROLLED_BACK},
    S.CANARY_50: {S.PRODUCTION, S.ROLLED_BACK},
    S.PRODUCTION: {S.ROLLED_BACK, S.RETIRED},
    S.ROLLED_BACK: {S.RETIRED, S.OFFLINE_EVAL},   # düzeltilip yeniden değerlendirilebilir
    S.REJECTED: {S.RETIRED},
    S.RETIRED: set(),
}
# admin onayı ZORUNLU olan geçişler (kritik ileri adımlar)
_ADMIN_REQUIRED = {S.TRAINING, S.SHADOW, S.CANARY_1, S.CANARY_5, S.CANARY_20,
                   S.CANARY_50, S.PRODUCTION}
# gate (benchmark/güvenlik) kanıtı ZORUNLU olan geçişler
_GATE_REQUIRED = {S.SECURITY_EVAL, S.SHADOW, S.CANARY_1, S.CANARY_5, S.CANARY_20,
                  S.CANARY_50, S.PRODUCTION}
CANARY_PCT = {S.CANARY_1: 1, S.CANARY_5: 5, S.CANARY_20: 20, S.CANARY_50: 50,
              S.PRODUCTION: 100}


@dataclass
class ModelVersion:
    model_id: str
    family: str                       # base|code|reasoning|mini|vision
    role: str                         # hawk_base|hawk_code|...
    version: str                      # v0.1|v0.2|v1.0
    base_model: str = ""              # e.g. the open foundation model
    adapter_type: str = "lora"        # lora|qlora|full
    tokenizer: str = ""
    training_dataset_version: str = ""
    dataset_sha256: str = ""          # frozen dataset içerik hash'i (kanıt)
    training_job_id: str = ""
    checkpoint: str = ""               # adapter path (R2 key/registry ref — GitHub'a ağırlık KONMAZ)
    adapter_sha256: str = ""          # adapter.tgz SHA256 (bütünlük/kanıt)
    training_config: dict[str, Any] = field(default_factory=dict)   # r/alpha/epoch/step/lr/seq/split
    final_loss: dict[str, Any] = field(default_factory=dict)        # {"train":..,"eval":..}
    quantization: str = ""            # awq|gptq|fp8|none
    context_length: int = 0
    supported_languages: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    benchmark_scores: dict[str, Any] = field(default_factory=dict)
    safety_scores: dict[str, Any] = field(default_factory=dict)
    shadow_results: dict[str, Any] = field(default_factory=dict)    # kategori-bazlı shadow özeti
    canary_results: dict[str, Any] = field(default_factory=dict)    # aşama/ok_total/fail_total
    latency: dict[str, Any] = field(default_factory=dict)
    throughput: Optional[float] = None
    vram_gb: Optional[float] = None
    license: str = ""
    provenance: str = ""
    created_at: float = 0.0
    status: ModelStatus = ModelStatus.DRAFT
    promoted_at: Optional[float] = None
    approved_by: str = ""             # son ileri-geçişi onaylayan admin
    rollback_by: str = ""             # rollback'i tetikleyen (admin veya "failsafe")
    rollback_target: str = ""         # geri dönülecek önceki production sürüm
    history: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @staticmethod
    def from_dict(d: dict) -> "ModelVersion":
        """Manifest JSON'dan ModelVersion (kalıcılık). Bilinmeyen alanları yok sayar."""
        fields = set(ModelVersion.__annotations__)
        kw = {k: v for k, v in d.items() if k in fields}
        kw["status"] = ModelStatus(d.get("status", "draft"))
        for t in ("supported_languages", "capabilities"):
            if t in kw and isinstance(kw[t], list):
                kw[t] = tuple(kw[t])
        return ModelVersion(**kw)


class RegistryError(Exception):
    pass


class ModelRegistry:
    def __init__(self):
        self._models: dict[str, ModelVersion] = {}
        self._prod: dict[str, str] = {}      # role -> production model_id

    def register(self, mv: ModelVersion) -> ModelVersion:
        if mv.model_id in self._models:
            raise RegistryError(f"model_id zaten kayıtlı: {mv.model_id}")
        mv.history.append(f"registered:{mv.status.value}")
        self._models[mv.model_id] = mv
        return mv

    def get(self, model_id: str) -> ModelVersion:
        return self._models[model_id]

    def list(self, *, role: Optional[str] = None,
             status: Optional[ModelStatus] = None) -> list[ModelVersion]:
        out = list(self._models.values())
        if role:
            out = [m for m in out if m.role == role]
        if status:
            out = [m for m in out if m.status == status]
        return out

    def production_version(self, role: str) -> Optional[str]:
        return self._prod.get(role)

    def advance(self, model_id: str, to: ModelStatus, *, admin_approved: bool = False,
                gate_passed: bool = False, reason: str = "", actor: str = "", now: float = 0.0) -> ModelVersion:
        mv = self._models[model_id]
        cur = mv.status
        if to not in _TRANSITIONS.get(cur, set()):
            raise RegistryError(f"geçersiz/atlanan geçiş: {cur.value} → {to.value}")
        if to in _ADMIN_REQUIRED and not admin_approved:
            raise RegistryError(f"{to.value} için admin onayı zorunlu")
        if to in _GATE_REQUIRED and not gate_passed:
            raise RegistryError(f"{to.value} için geçen gate (benchmark/güvenlik) zorunlu")
        mv.status = to
        if to in _ADMIN_REQUIRED and actor:
            mv.approved_by = actor
        mv.history.append(f"{cur.value}->{to.value}:{reason or 'ok'}{(' by='+actor) if actor else ''}")
        if to == ModelStatus.PRODUCTION:
            # önceki production'ı rollback_target yap, retire etmeden işaretle
            prev = self._prod.get(mv.role)
            if prev and prev != model_id:
                mv.rollback_target = prev
            self._prod[mv.role] = model_id
            mv.promoted_at = now
        return mv

    def rollback(self, model_id: str, *, reason: str = "", admin_approved: bool = False,
                 fail_safe: bool = False, actor: str = "", now: float = 0.0) -> dict:
        """Rollback: admin onayı VEYA kritik-güvenlik fail-safe. Production ise önceki
        sürüme (rollback_target) döner."""
        mv = self._models[model_id]
        if mv.status not in (S.CANARY_1, S.CANARY_5, S.CANARY_20, S.CANARY_50, S.PRODUCTION):
            raise RegistryError("yalnız canary/production rollback edilebilir")
        if not admin_approved and not fail_safe:
            raise RegistryError("rollback admin onayı veya fail-safe (kritik güvenlik) ister")
        was_prod = self._prod.get(mv.role) == model_id
        target = mv.rollback_target or ""
        mv.status = S.ROLLED_BACK
        mv.rollback_by = actor or ("failsafe" if fail_safe else "admin")
        mv.history.append(f"rolled_back:{'failsafe' if fail_safe else 'admin'}:{reason}{(' by='+actor) if actor else ''}")
        if was_prod:
            del self._prod[mv.role]
            if target and target in self._models:
                self._prod[mv.role] = target   # önceki production'a dön
        return {"rolled_back": model_id, "restored": self._prod.get(mv.role),
                "fail_safe": fail_safe, "reason": reason}

    def retire(self, model_id: str) -> ModelVersion:
        mv = self._models[model_id]
        if mv.status not in (S.ROLLED_BACK, S.REJECTED, S.PRODUCTION):
            raise RegistryError("yalnız rolled_back/rejected/production retire edilebilir")
        if self._prod.get(mv.role) == model_id:
            del self._prod[mv.role]
        mv.status = S.RETIRED
        mv.history.append("retired")
        return mv
