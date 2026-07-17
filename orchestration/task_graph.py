"""
Task DAG — görevleri düz liste değil bağımlılık grafiği olarak yürütmek için.

Denetim bulgusu: bugün gerçek DAG yok; hawk_core_tasks yalnız goal_id FK taşıyor,
kilit/lease yok, restart'ta 'running' görevler orphan kalıyor, idempotency yok.
Bu modül saf, in-memory, deterministik DAG çekirdeğidir (persistans katmanı ayrı,
onaylı migration turunda eklenecek). `now` daima enjekte edilir → deterministik.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


class TaskStatus(str, Enum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    WAITING_TOOL = "waiting_tool"
    WAITING_REVIEW = "waiting_review"
    PAUSED = "paused"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
    COMPLETED = "completed"


TERMINAL = frozenset({TaskStatus.COMPLETED, TaskStatus.CANCELLED})
FAILED_STATES = frozenset({TaskStatus.FAILED, TaskStatus.BLOCKED})
ACTIVE = frozenset({TaskStatus.RUNNING, TaskStatus.WAITING_TOOL, TaskStatus.WAITING_REVIEW})


@dataclass
class TaskNode:
    task_id: str
    title: str
    objective: str
    user_scope: str
    project_scope: str = ""
    parent_task_id: str | None = None
    dependencies: tuple[str, ...] = ()
    assigned_agent_id: str | None = None
    status: TaskStatus = TaskStatus.PENDING
    priority: int = 5
    input: dict[str, Any] = field(default_factory=dict)
    expected_output_schema: dict[str, Any] = field(default_factory=dict)
    result: Any = None
    evidence: tuple[str, ...] = ()          # evidence_id listesi
    reviewer_result: dict[str, Any] | None = None
    retry_count: int = 0
    max_retry: int = 2
    token_used: int = 0
    cost_used: float = 0.0
    started_at: float | None = None
    heartbeat_at: float | None = None
    completed_at: float | None = None
    checkpoint: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    idempotency_key: str | None = None
    lease_owner: str | None = None
    lease_expires_at: float | None = None

    @property
    def user_scope_hash(self) -> str:
        return hashlib.sha256(("u:" + self.user_scope).encode()).hexdigest()[:16]

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL


class DagError(Exception):
    pass


class TaskGraph:
    """Tek görev-ağacı. user_scope/project_scope izolasyonu düğüm bazında taşınır."""

    def __init__(self, user_scope: str, project_scope: str = ""):
        self.user_scope = user_scope
        self.project_scope = project_scope
        self._nodes: dict[str, TaskNode] = {}
        self._idem: dict[str, str] = {}   # idempotency_key -> task_id

    # -- yapı --
    def add_task(self, node: TaskNode) -> TaskNode:
        # izolasyon: düğüm ağacın scope'unu taşımalı
        if node.user_scope != self.user_scope:
            raise DagError("cross-user task ekleme reddedildi")
        if (node.project_scope or "") != (self.project_scope or ""):
            raise DagError("cross-project task ekleme reddedildi")
        # idempotency: aynı anahtar → mevcut düğümü döndür (duplicate yok)
        if node.idempotency_key:
            if node.idempotency_key in self._idem:
                return self._nodes[self._idem[node.idempotency_key]]
            self._idem[node.idempotency_key] = node.task_id
        if node.task_id in self._nodes:
            raise DagError(f"duplicate task_id {node.task_id}")
        for dep in node.dependencies:
            if dep not in self._nodes:
                raise DagError(f"bilinmeyen dependency {dep}")
        self._nodes[node.task_id] = node
        self._assert_acyclic()
        return node

    def add_dependency(self, task_id: str, depends_on: str) -> None:
        if task_id not in self._nodes or depends_on not in self._nodes:
            raise DagError("bilinmeyen düğüm")
        if task_id == depends_on:
            raise DagError("kendine bağımlılık")
        n = self._nodes[task_id]
        if depends_on not in n.dependencies:
            n.dependencies = tuple(n.dependencies) + (depends_on,)
        self._assert_acyclic()

    def get(self, task_id: str) -> TaskNode:
        return self._nodes[task_id]

    def all(self) -> list[TaskNode]:
        return list(self._nodes.values())

    # -- döngü tespiti --
    def _assert_acyclic(self) -> None:
        WHITE, GREY, BLACK = 0, 1, 2
        color = {tid: WHITE for tid in self._nodes}

        def visit(tid: str) -> None:
            color[tid] = GREY
            for dep in self._nodes[tid].dependencies:
                if color[dep] == GREY:
                    raise DagError(f"döngü: {tid} -> {dep}")
                if color[dep] == WHITE:
                    visit(dep)
            color[tid] = BLACK

        for tid in self._nodes:
            if color[tid] == WHITE:
                visit(tid)

    # -- yürütme mantığı --
    def ready_tasks(self) -> list[TaskNode]:
        """Tüm bağımlılıkları COMPLETED olan, henüz başlamamış düğümler
        (yüksek öncelik önce)."""
        out = []
        for n in self._nodes.values():
            if n.status not in (TaskStatus.PENDING, TaskStatus.READY):
                continue
            if all(self._nodes[d].status == TaskStatus.COMPLETED for d in n.dependencies):
                out.append(n)
        out.sort(key=lambda x: (-x.priority, x.task_id))
        return out

    def mark(self, task_id: str, status: TaskStatus, *, now: float,
             error: str | None = None, result: Any = None) -> None:
        n = self._nodes[task_id]
        if n.status in TERMINAL and status != n.status:
            raise DagError(f"{task_id} terminal ({n.status.value}); değiştirilemez")
        n.status = status
        if status == TaskStatus.RUNNING and n.started_at is None:
            n.started_at = now
        if status == TaskStatus.COMPLETED:
            n.completed_at = now
            if result is not None:
                n.result = result
        if status in FAILED_STATES and error:
            n.error = error
        if status == TaskStatus.CANCELLED:
            n.completed_at = now

    def heartbeat(self, task_id: str, *, now: float, owner: str, lease_s: float = 60.0) -> None:
        n = self._nodes[task_id]
        n.heartbeat_at = now
        n.lease_owner = owner
        n.lease_expires_at = now + lease_s

    # -- crash recovery / orphan sweep --
    def sweep_orphans(self, *, now: float) -> list[str]:
        """Lease'i dolmuş 'running/waiting' düğümleri kurtarır:
        retry hakkı varsa PENDING'e (yeniden dispatch), yoksa FAILED (dead-letter).
        Döndürür: etkilenen task_id listesi."""
        affected = []
        for n in self._nodes.values():
            if n.status not in ACTIVE:
                continue
            if n.lease_expires_at is not None and n.lease_expires_at > now:
                continue  # lease hâlâ geçerli
            affected.append(n.task_id)
            if n.retry_count < n.max_retry:
                n.retry_count += 1
                n.status = TaskStatus.PENDING
                n.lease_owner = None
                n.lease_expires_at = None
                n.error = "orphan_recovered"
            else:
                n.status = TaskStatus.FAILED
                n.error = "orphan_max_retry"
        return affected

    # -- iptal (ağaç dallı) --
    def cancel_subtree(self, task_id: str, *, now: float) -> list[str]:
        cancelled = []
        stack = [task_id]
        while stack:
            tid = stack.pop()
            n = self._nodes.get(tid)
            if not n or n.status in TERMINAL:
                continue
            n.status = TaskStatus.CANCELLED
            n.completed_at = now
            cancelled.append(tid)
            for m in self._nodes.values():
                if m.parent_task_id == tid:
                    stack.append(m.task_id)
        return cancelled

    def pause(self, task_id: str) -> None:
        n = self._nodes[task_id]
        if n.status in TERMINAL:
            raise DagError("terminal görev duraklatılamaz")
        n.status = TaskStatus.PAUSED

    def resume(self, task_id: str) -> None:
        n = self._nodes[task_id]
        if n.status != TaskStatus.PAUSED:
            raise DagError("yalnız paused görev sürdürülebilir")
        n.status = TaskStatus.PENDING

    # -- durum --
    def is_complete(self) -> bool:
        return all(n.status in TERMINAL or n.status in FAILED_STATES for n in self._nodes.values())

    def summary(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for n in self._nodes.values():
            out[n.status.value] = out.get(n.status.value, 0) + 1
        return out


def make_task_id(prefix: str, seed: str) -> str:
    """Deterministik, çakışmasız task_id (Date/random kullanmadan)."""
    h = hashlib.sha256(seed.encode()).hexdigest()[:12]
    return f"{prefix}_{h}"
