"""
Model lifecycle registry + shadow/canary state machine (Section 19-20).

HAWK production'da kendi ağırlıklarını OTOMATİK değiştirmez. Yeni model/policy şu
kapıları SIRAYLA geçmeden production'a çıkamaz ve her aşamada rollback mümkündür:
  unit → integration → security → benchmark → shadow → canary%1 → %5 → %20 → %50 → full

Her ilerleme AÇIK onay + geçen gate kanıtı ister. Otomatik promotion YOKTUR.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Stage(str, Enum):
    REGISTERED = "registered"
    UNIT = "unit"
    INTEGRATION = "integration"
    SECURITY = "security"
    BENCHMARK = "benchmark"
    SHADOW = "shadow"
    CANARY_1 = "canary_1"
    CANARY_5 = "canary_5"
    CANARY_20 = "canary_20"
    CANARY_50 = "canary_50"
    PRODUCTION = "production"
    ROLLED_BACK = "rolled_back"


# İzinli ileri geçişler (sıra atlanamaz)
_ORDER = [
    Stage.REGISTERED, Stage.UNIT, Stage.INTEGRATION, Stage.SECURITY, Stage.BENCHMARK,
    Stage.SHADOW, Stage.CANARY_1, Stage.CANARY_5, Stage.CANARY_20, Stage.CANARY_50,
    Stage.PRODUCTION,
]


@dataclass
class ModelVersion:
    version: str
    base_model: str
    role: str                       # hawk_base|hawk_code|...
    stage: Stage = Stage.REGISTERED
    provenance: str = ""
    dataset_version: str = ""
    gates_passed: list[str] = field(default_factory=list)
    canary_pct: int = 0
    notes: str = ""


class PromotionError(Exception):
    pass


class ModelLifecycle:
    def __init__(self):
        self._versions: dict[str, ModelVersion] = {}
        self._prod: dict[str, str] = {}   # role -> production version

    def register(self, version: str, base_model: str, role: str, *,
                 provenance: str = "", dataset_version: str = "") -> ModelVersion:
        if version in self._versions:
            raise PromotionError(f"sürüm zaten kayıtlı: {version}")
        mv = ModelVersion(version=version, base_model=base_model, role=role,
                          provenance=provenance, dataset_version=dataset_version)
        self._versions[version] = mv
        return mv

    def get(self, version: str) -> ModelVersion:
        return self._versions[version]

    def _canary_pct(self, stage: Stage) -> int:
        return {Stage.CANARY_1: 1, Stage.CANARY_5: 5, Stage.CANARY_20: 20,
                Stage.CANARY_50: 50, Stage.PRODUCTION: 100}.get(stage, 0)

    def promote(self, version: str, to: Stage, *, gate_passed: bool,
                admin_approved: bool, evidence: str = "") -> ModelVersion:
        """Bir sonraki aşamaya AÇIK onay + geçen gate ile ilerlet. Sıra atlanamaz."""
        mv = self._versions[version]
        if mv.stage == Stage.ROLLED_BACK:
            raise PromotionError("rollback edilmiş sürüm ilerletilemez")
        if not admin_approved:
            raise PromotionError("admin onayı olmadan promotion yok")
        if not gate_passed:
            raise PromotionError(f"{to.value} gate geçilmeden ilerletilemez")
        cur_i = _ORDER.index(mv.stage) if mv.stage in _ORDER else -1
        try:
            to_i = _ORDER.index(to)
        except ValueError:
            raise PromotionError(f"geçersiz hedef aşama {to}")
        if to_i != cur_i + 1:
            raise PromotionError(f"aşama atlanamaz: {mv.stage.value} → {to.value}")
        mv.stage = to
        mv.canary_pct = self._canary_pct(to)
        mv.gates_passed.append(f"{to.value}:{evidence or 'ok'}")
        if to == Stage.PRODUCTION:
            self._prod[mv.role] = version
        return mv

    def rollback(self, version: str, reason: str = "") -> ModelVersion:
        mv = self._versions[version]
        mv.stage = Stage.ROLLED_BACK
        mv.canary_pct = 0
        mv.notes = f"rollback: {reason}"
        if self._prod.get(mv.role) == version:
            del self._prod[mv.role]
        return mv

    def production_version(self, role: str) -> Optional[str]:
        return self._prod.get(role)
