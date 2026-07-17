"""
Agent performance & operational-learning.

Denetim/direktif (Section 14-15): her ajan çalışması için performans kaydı tut —
AMA ham kullanıcı mesajı / prompt / cevap İÇERİĞİ tabloya YAZILMAZ. Yalnız hash'li
scope + metrik. Analiz yalnız ÖNERİ üretir; production policy'yi otomatik değiştirmez.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, asdict
from typing import Any


def _scrub(rec: dict) -> dict:
    """Ham içerik alanlarını (varsa) düşür — tabloya asla prompt/cevap girmez."""
    banned = {"prompt", "raw_prompt", "message", "response", "answer",
              "content", "system_prompt", "chain_of_thought", "scratchpad"}
    return {k: v for k, v in rec.items() if k not in banned}


@dataclass
class PerformanceRecord:
    execution_id: str
    task_id: str
    user_scope_hash: str
    project_scope_hash: str
    agent_role: str
    model_role: str
    actual_model: str
    provider: str
    tools: tuple[str, ...] = ()
    tokens: int = 0
    cost: float = 0.0
    latency_ms: int = 0
    success: bool = False
    error_type: str = ""
    retry_count: int = 0
    evidence_count: int = 0
    reviewer_score: float = 0.0
    security_score: float = 0.0
    final_accepted: bool = False
    user_feedback: str = ""
    created_at: float = 0.0

    def to_row(self) -> dict:
        return _scrub(asdict(self))


class PerformanceStore:
    def __init__(self):
        self._rows: list[PerformanceRecord] = []

    def record(self, **kw) -> PerformanceRecord:
        # ham içerik alanı gelirse düşür (savunma)
        kw = _scrub(kw)
        # hash zorunlu: düz email verilirse hash'le
        if "user_scope_hash" not in kw and "user_scope" in kw:
            kw["user_scope_hash"] = hashlib.sha256(("u:" + kw.pop("user_scope")).encode()).hexdigest()[:16]
        rec = PerformanceRecord(**{k: v for k, v in kw.items()
                                   if k in PerformanceRecord.__annotations__})
        self._rows.append(rec)
        return rec

    def all(self) -> list[PerformanceRecord]:
        return list(self._rows)

    # -- operasyonel öğrenme: yalnız ÖNERİ üretir --
    def propose(self, *, min_samples: int = 5) -> list[dict]:
        proposals = []
        # rol × model başarı oranı
        by_key: dict[tuple[str, str], list[PerformanceRecord]] = {}
        for r in self._rows:
            by_key.setdefault((r.agent_role, r.model_role), []).append(r)
        for (role, model), rows in by_key.items():
            if len(rows) < min_samples:
                continue
            succ = sum(1 for r in rows if r.success) / len(rows)
            avg_cost = sum(r.cost for r in rows) / len(rows)
            avg_retry = sum(r.retry_count for r in rows) / len(rows)
            if succ < 0.5:
                proposals.append(self._proposal(
                    "reroute_role_model",
                    {"role": role, "model_role": model, "success_rate": round(succ, 3)},
                    {"model_role": "external_expert"},
                    len(rows), f"{role}/{model} başarı %{succ*100:.0f} — daha güçlü modele yönlendir",
                    confidence=min(0.9, len(rows) / 20), risk="low"))
            if avg_retry > 1.5:
                proposals.append(self._proposal(
                    "reduce_retry_waste",
                    {"role": role, "avg_retry": round(avg_retry, 2)},
                    {"max_retry": 1},
                    len(rows), f"{role} ortalama retry {avg_retry:.1f} — gereksiz maliyet",
                    confidence=0.6, risk="low"))
        return proposals

    @staticmethod
    def _proposal(ptype, current, suggested, n, benefit, *, confidence, risk) -> dict:
        return {
            "proposal_type": ptype,
            "current_policy": current,
            "suggested_policy": suggested,
            "evidence_count": n,
            "expected_benefit": benefit,
            "confidence": round(confidence, 3),
            "risk": risk,
            "benchmark_required": True,
            "admin_approval_required": True,   # production ASLA otomatik değişmez
        }
