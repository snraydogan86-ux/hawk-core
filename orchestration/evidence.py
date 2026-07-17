"""
Evidence store — kanıt tabanlı çelişki çözümü (Section 7) için tipli kanıt deposu.

Kural: kanıtsız iddia final cevaba alınmaz. Çoğunluk oyu TEK BAŞINA karar değildir.
Öncelik: gerçek test çıktısı > log/diff/dosya > resmî kaynak > tekrarlanabilir deney
        > bağımsız reviewer > güven skoru > çoğunluk görüşü.

Kanıt saklanırken ham secret/PII yazılmaz — looks_unsafe guard (varsa fact_extractor)
ile redakte edilir. Depo user_scope/project_scope hash'i ile izole edilir.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class EvidenceType(str, Enum):
    TEST_OUTPUT = "test_output"     # en güçlü
    LOG = "log"
    DIFF = "diff"
    FILE = "file"
    OFFICIAL_SOURCE = "official_source"
    REPRO = "repro"
    REVIEWER = "reviewer"
    CONFIDENCE = "confidence"
    CLAIM = "claim"                 # en zayıf (kanıtsız iddia)


# Section 7 öncelik ağırlıkları (yüksek = daha güvenilir)
_WEIGHT = {
    EvidenceType.TEST_OUTPUT: 100,
    EvidenceType.LOG: 80,
    EvidenceType.DIFF: 80,
    EvidenceType.FILE: 70,
    EvidenceType.OFFICIAL_SOURCE: 60,
    EvidenceType.REPRO: 55,
    EvidenceType.REVIEWER: 40,
    EvidenceType.CONFIDENCE: 20,
    EvidenceType.CLAIM: 5,
}


import re as _re

# YALNIZ gerçek secret desenleri — meşru KOD/diff redakte edilmez (kanıt olarak saklanabilir).
# (fact_extractor.looks_unsafe tüm kodu 'unsafe' sayar; kanıt için fazla geniş → kullanılmaz.)
_SECRET_RX = _re.compile(
    r"(sk-[A-Za-z0-9\-_]{8,}"
    r"|AKIA[0-9A-Z]{12,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"|(?i:(?:api[_-]?key|password|passwd|secret|access[_-]?token|bearer)\s*[=:]\s*)\S{6,})")


def _redact(text: str) -> tuple[str, bool]:
    """Ham SECRET'i redakte eder (kod/diff korunur). (redacted_text, secret_found)."""
    if not text:
        return text, False
    if _SECRET_RX.search(text):
        return _SECRET_RX.sub("[REDACTED_SECRET]", text), True
    return text, False


@dataclass
class Evidence:
    evidence_id: str
    task_id: str
    agent_id: str
    etype: EvidenceType
    summary: str
    detail: str = ""
    reproducible: bool = False
    user_scope_hash: str = ""
    project_scope_hash: str = ""
    redacted: bool = False

    @property
    def weight(self) -> int:
        w = _WEIGHT[self.etype]
        if self.reproducible and self.etype in (EvidenceType.TEST_OUTPUT, EvidenceType.REPRO):
            w += 10
        return w


class EvidenceStore:
    def __init__(self):
        self._by_task: dict[str, list[Evidence]] = {}
        self._conflicts: list[dict[str, Any]] = []

    def add(self, *, task_id: str, agent_id: str, etype: EvidenceType,
            summary: str, detail: str = "", reproducible: bool = False,
            user_scope: str = "", project_scope: str = "") -> Evidence:
        rd_summary, u1 = _redact(summary)
        rd_detail, u2 = _redact(detail)
        seed = f"{task_id}|{agent_id}|{etype.value}|{len(self._by_task.get(task_id, []))}"
        ev = Evidence(
            evidence_id="ev_" + hashlib.sha256(seed.encode()).hexdigest()[:12],
            task_id=task_id, agent_id=agent_id, etype=etype,
            summary=rd_summary, detail=rd_detail, reproducible=reproducible,
            user_scope_hash=hashlib.sha256(("u:" + user_scope).encode()).hexdigest()[:16] if user_scope else "",
            project_scope_hash=hashlib.sha256(("p:" + project_scope).encode()).hexdigest()[:16] if project_scope else "",
            redacted=u1 or u2,
        )
        self._by_task.setdefault(task_id, []).append(ev)
        return ev

    def for_task(self, task_id: str) -> list[Evidence]:
        return list(self._by_task.get(task_id, []))

    def has_hard_evidence(self, task_id: str) -> bool:
        """Gerçek test/log/diff/dosya kanıtı var mı? (kanıtsız PASS engeli)"""
        strong = {EvidenceType.TEST_OUTPUT, EvidenceType.LOG,
                  EvidenceType.DIFF, EvidenceType.FILE, EvidenceType.REPRO}
        return any(e.etype in strong for e in self._by_task.get(task_id, []))

    def resolve(self, task_id: str, claims: list[dict[str, Any]]) -> dict[str, Any]:
        """Çelişkili claim'leri kanıt ağırlığına göre çözer.

        claims: [{"verdict": <hashable>, "by": agent_id, "evidence_ids": [...]}]
        Karar = en yüksek toplam kanıt ağırlığına sahip verdict.
        Beraberlik/kanıtsızlık açıkça kaydedilir; çoğunluk tek başına kazanamaz.
        """
        evs = {e.evidence_id: e for e in self._by_task.get(task_id, [])}
        scored: dict[Any, int] = {}
        counts: dict[Any, int] = {}
        for c in claims:
            v = c.get("verdict")
            counts[v] = counts.get(v, 0) + 1
            w = sum(evs[eid].weight for eid in c.get("evidence_ids", []) if eid in evs)
            scored[v] = scored.get(v, 0) + w

        if not scored:
            return {"decided": None, "reason": "no_claims", "conflict": False}

        best = max(scored.items(), key=lambda kv: kv[1])
        best_v, best_w = best
        tie = [v for v, w in scored.items() if w == best_w]
        conflict = len(scored) > 1

        # Kanıt yoksa (tüm ağırlıklar 0) çoğunluk oyu KARAR sayılmaz.
        if best_w == 0:
            rec = {"decided": None, "reason": "no_evidence_majority_only",
                   "conflict": conflict, "counts": counts, "weights": scored}
            if conflict:
                self._conflicts.append({"task_id": task_id, **rec})
            return rec

        if len(tie) > 1:
            rec = {"decided": None, "reason": "evidence_tie", "conflict": True,
                   "tie": tie, "weights": scored}
            self._conflicts.append({"task_id": task_id, **rec})
            return rec

        rec = {"decided": best_v, "reason": "evidence_weight", "conflict": conflict,
               "winning_weight": best_w, "weights": scored, "counts": counts}
        if conflict:
            self._conflicts.append({"task_id": task_id, **rec})
        return rec

    def conflicts(self) -> list[dict[str, Any]]:
        return list(self._conflicts)
