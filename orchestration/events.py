"""
Event stream — telefon/web/PC senkronizasyonu için tek kaynak (Section 12).

Aynı task_id tüm yüzeylerde kullanılır; her yüzey aynı olay akışını görür.
Olaylar user_scope ile izole edilir — bir abonelik başka kullanıcının olaylarını
GÖREMEZ. Bu in-memory referans uygulamadır (persistans/SSE köprüsü ayrı katman).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


class EventType(str, Enum):
    TASK_CREATED = "task_created"
    PLAN_READY = "plan_ready"
    AGENT_STARTED = "agent_started"
    TOOL_CALLED = "tool_called"
    EVIDENCE_ADDED = "evidence_added"
    REVIEW_STARTED = "review_started"
    TASK_PAUSED = "task_paused"
    TASK_RESUMED = "task_resumed"
    TASK_FAILED = "task_failed"
    TASK_COMPLETED = "task_completed"
    TASK_CANCELLED = "task_cancelled"
    WAITING_DEVICE = "waiting_device"     # PC offline → yanlış "çalışıyor" gösterme


# Kullanıcıya ASLA yayınlanmayacak alan adları (gizli chain-of-thought vb.).
_SENSITIVE_FIELDS = frozenset({
    "system_prompt", "secret", "api_key", "raw_prompt",
    "chain_of_thought", "scratchpad", "private_scratch",
})


@dataclass
class Event:
    seq: int
    etype: EventType
    task_id: str
    user_scope_hash: str
    at: float
    surface: str = "server"               # server|phone|web|pc|workspace
    payload: dict[str, Any] = field(default_factory=dict)


def _scrub(payload: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in (payload or {}).items() if k not in _SENSITIVE_FIELDS}


class EventBus:
    """user_scope-izole, sıralı (seq) event bus. Aynı task_id çok yüzeyde okunur."""

    def __init__(self):
        self._events: list[Event] = []
        self._seq = 0
        self._subs: list[tuple[str, Callable[[Event], None]]] = []

    def publish(self, *, etype: EventType, task_id: str, user_scope: str,
                now: float, surface: str = "server",
                payload: dict[str, Any] | None = None) -> Event:
        self._seq += 1
        ev = Event(
            seq=self._seq, etype=etype, task_id=task_id,
            user_scope_hash=hashlib.sha256(("u:" + user_scope).encode()).hexdigest()[:16],
            at=now, surface=surface, payload=_scrub(payload or {}),
        )
        self._events.append(ev)
        for uh, cb in self._subs:
            if uh == ev.user_scope_hash:
                try:
                    cb(ev)
                except Exception:
                    pass
        return ev

    def subscribe(self, *, user_scope: str, callback: Callable[[Event], None]) -> None:
        uh = hashlib.sha256(("u:" + user_scope).encode()).hexdigest()[:16]
        self._subs.append((uh, callback))

    def since(self, *, user_scope: str, after_seq: int = 0,
              task_id: str | None = None) -> list[Event]:
        """Bir kullanıcının kendi olayları (çok-yüzey senkron için cursor tabanlı)."""
        uh = hashlib.sha256(("u:" + user_scope).encode()).hexdigest()[:16]
        out = [e for e in self._events
               if e.user_scope_hash == uh and e.seq > after_seq
               and (task_id is None or e.task_id == task_id)]
        return out

    def all_seq(self) -> int:
        return self._seq
