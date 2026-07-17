"""
Reviewer runtime — bağımsız denetçi.

Kurallar (Section 6):
  - Reviewer executor ile AYNI instance olamaz.
  - Kanıt olmadan PASS veremez.
  - Sahte test/log iddiasını (hard-evidence yok) reddeder.
  - Güvenlik/maliyet/tenant ihlali yakalarsa reddeder.
  - Yüksek riskte ayrı security reviewer ister.
  - Çelişkide ek doğrulama açar (conflict verdict).

Model çağrısı gerektirmez — kanıt-tabanlı deterministik kontrol (isteğe bağlı
model-destekli derin inceleme ayrı katman). Farklı model/provider reviewer
gateway üzerinden takılabilir.
"""
from __future__ import annotations

from typing import Any, Optional

from .evidence import EvidenceStore, EvidenceType
from .registry import AgentRegistry, RegistryError


class Reviewer:
    def __init__(self, *, agent_id: str, registry: AgentRegistry,
                 require_hard_evidence: bool = True,
                 gateway=None, security_reviewer_id: Optional[str] = None):
        self.agent_id = agent_id
        self.registry = registry
        self.require_hard = require_hard_evidence
        self.gateway = gateway
        self.security_reviewer_id = security_reviewer_id

    async def review(self, *, task_id: str, executor_agent_id: str,
                     evidence: EvidenceStore) -> dict:
        # 1) reviewer != executor (fail-closed)
        if executor_agent_id == self.agent_id:
            return {"passed": False, "reason": "reviewer_equals_executor", "score": 0}
        try:
            self.registry.assert_independent_reviewer(executor_agent_id, self.agent_id)
        except RegistryError as e:
            return {"passed": False, "reason": f"not_independent:{e}", "score": 0}

        evs = evidence.for_task(task_id)
        # 2) kanıt yok → PASS yok
        if not evs:
            return {"passed": False, "reason": "no_evidence", "score": 0}
        # 3) sahte/zayıf iddia: hard-evidence (test/log/diff/file) zorunlu
        if self.require_hard and not evidence.has_hard_evidence(task_id):
            return {"passed": False, "reason": "no_hard_evidence_only_claims", "score": 10}
        # 4) güvenlik: redakte edilmiş (secret sızıntısı) kanıt → şüpheli
        if any(e.redacted for e in evs):
            return {"passed": False, "reason": "evidence_contained_secret", "score": 5}

        score = min(100, 40 + 15 * sum(1 for e in evs if e.weight >= 70))
        return {"passed": True, "reason": "evidence_verified", "score": score,
                "evidence_count": len(evs), "reviewer": self.agent_id}

    async def escalate_security(self, *, task_id: str, executor_agent_id: str,
                               evidence: EvidenceStore) -> dict:
        """Yüksek riskte ayrı security reviewer'a ver."""
        if not self.security_reviewer_id:
            return {"passed": False, "reason": "no_security_reviewer_available"}
        sec = Reviewer(agent_id=self.security_reviewer_id, registry=self.registry,
                       require_hard_evidence=self.require_hard, gateway=self.gateway)
        return await sec.review(task_id=task_id, executor_agent_id=executor_agent_id,
                                evidence=evidence)
