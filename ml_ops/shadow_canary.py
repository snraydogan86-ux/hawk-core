"""
Shadow mode + canary progression + rollback kayıtları.

Shadow: kullanıcıya production cevabı gider; yeni model arka planda üretir; kıyaslanır;
PII/secret loglanmaz; shadow bütçesi ayrı. Canary: %1→%5→%20→%50→production; her aşamada
metrik izlenir; eşik aşılırsa OTOMATİK ROLLBACK ÖNERİSİ üretilir (uygulama admin onayı
veya kritik güvenlikte fail-safe).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


@dataclass
class ShadowRecord:
    model_id: str
    samples: int = 0
    quality_match: int = 0            # production ile kalite eşleşmesi
    tool_match: int = 0
    security_incidents: int = 0
    total_latency_ms: float = 0.0
    est_cost_usd: float = 0.0
    budget_usd: float = 0.0
    pii_logged: bool = False          # DAİMA False olmalı (invariant)

    def add(self, *, quality_ok: bool, tool_ok: bool, security_incident: bool,
            latency_ms: float, cost: float) -> None:
        self.samples += 1
        self.quality_match += int(quality_ok)
        self.tool_match += int(tool_ok)
        self.security_incidents += int(security_incident)
        self.total_latency_ms += latency_ms
        self.est_cost_usd += cost

    def within_budget(self) -> bool:
        return self.budget_usd <= 0 or self.est_cost_usd <= self.budget_usd

    def summary(self) -> dict:
        n = max(1, self.samples)
        return {"samples": self.samples,
                "quality_match_rate": round(self.quality_match / n, 4),
                "tool_match_rate": round(self.tool_match / n, 4),
                "security_incidents": self.security_incidents,
                "avg_latency_ms": round(self.total_latency_ms / n, 1),
                "est_cost_usd": round(self.est_cost_usd, 4),
                "within_budget": self.within_budget(),
                "pii_logged": self.pii_logged}


# canary eşikleri (aşılırsa rollback önerisi)
CANARY_THRESHOLDS = {
    "error_rate": 0.05,
    "hallucination_rate": 0.10,
    "security_incidents": 0,          # >0 → kritik
    "fallback_rate": 0.30,
}


@dataclass
class CanaryRecord:
    model_id: str
    stage_pct: int                    # 1|5|20|50
    error_rate: float = 0.0
    hallucination_rate: float = 0.0
    security_incidents: int = 0
    tool_success_rate: float = 1.0
    avg_latency_ms: float = 0.0
    cost_usd: float = 0.0
    user_feedback: float = 0.0
    fallback_rate: float = 0.0


def evaluate_canary(rec: CanaryRecord) -> dict:
    """Eşik aşımı → rollback önerisi. Güvenlik ihlali → fail-safe önerisi."""
    breaches = []
    if rec.error_rate > CANARY_THRESHOLDS["error_rate"]:
        breaches.append(f"error_rate:{rec.error_rate:.3f}")
    if rec.hallucination_rate > CANARY_THRESHOLDS["hallucination_rate"]:
        breaches.append(f"hallucination:{rec.hallucination_rate:.3f}")
    if rec.fallback_rate > CANARY_THRESHOLDS["fallback_rate"]:
        breaches.append(f"fallback:{rec.fallback_rate:.3f}")
    critical = rec.security_incidents > CANARY_THRESHOLDS["security_incidents"]
    if critical:
        breaches.append(f"security_incidents:{rec.security_incidents}")
    return {
        "model_id": rec.model_id, "stage_pct": rec.stage_pct,
        "breaches": breaches,
        "rollback_recommended": len(breaches) > 0,
        "fail_safe_rollback": critical,      # kritik güvenlik → admin beklemeden
        "promote_ok": len(breaches) == 0,
    }
