"""
Supervisor runtime — görevi DAG'a böler, uygun ajanı seçer, paralel-hazır node'ları
çalıştırır, heartbeat/lease + timeout/retry uygular, kanıt toplar, review görevi
oluşturur, başarısızlıkta replan yapar ve final synthesizer'a YALNIZ doğrulanmış
sonuçları verir.

Yalnız veri sınıfı DEĞİL — gerçek çalışan orkestrasyondur. Model çağrıları executor
callback'i üzerinden gelir (E2E'de gerçek dosya/pytest ajanları, testte deterministik
mock). Bütçe/limit BudgetTree ile ağaç-genelinde uygulanır.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from .task_graph import TaskGraph, TaskNode, TaskStatus
from .policy import BudgetTree, AgentLimits, DEFAULT_LIMITS
from .evidence import EvidenceStore, EvidenceType
from .events import EventBus, EventType
from .registry import AgentRegistry


# executor(node, spec) -> ExecResult
@dataclass
class ExecResult:
    ok: bool
    result: Any = None
    evidence: list[dict] = field(default_factory=list)   # [{etype,summary,detail,reproducible}]
    tokens: int = 0
    cost: float = 0.0
    error: Optional[str] = None


Executor = Callable[[TaskNode, Any], Awaitable[ExecResult]]


class Supervisor:
    def __init__(self, *, user_scope: str, project_scope: str = "",
                 registry: AgentRegistry, evidence: EvidenceStore,
                 events: EventBus, limits: AgentLimits | None = None,
                 now_fn: Callable[[], float] | None = None):
        self.user_scope = user_scope
        self.project_scope = project_scope
        self.registry = registry
        self.evidence = evidence
        self.events = events
        self.budget = BudgetTree(limits or DEFAULT_LIMITS)
        self.graph = TaskGraph(user_scope, project_scope)
        self._now = now_fn or time.monotonic
        self._executors: dict[str, Executor] = {}   # role -> executor
        self._reviewer = None                        # Reviewer instance (opsiyonel)
        self._cancelled = False
        self._paused = False
        self._seq = 0
        self.role_runs: list[dict] = []              # performans/telemetri

    # -- kurulum --
    def set_executor(self, role: str, fn: Executor) -> None:
        self._executors[role] = fn

    def set_reviewer(self, reviewer) -> None:
        self._reviewer = reviewer

    def add_node(self, node: TaskNode) -> TaskNode:
        return self.graph.add_task(node)

    def _emit(self, etype: EventType, task_id: str, **payload) -> None:
        self._seq += 1
        self.events.publish(etype=etype, task_id=task_id, user_scope=self.user_scope,
                            now=self._now(), payload=payload)

    def cancel(self) -> list[str]:
        self._cancelled = True
        out = []
        for n in self.graph.all():
            if not n.is_terminal:
                out += self.graph.cancel_subtree(n.task_id, now=self._now())
        return list(dict.fromkeys(out))

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    # -- tek node çalıştır --
    async def _run_node(self, node: TaskNode) -> None:
        spec = self.registry.get(node.assigned_agent_id) if node.assigned_agent_id else None
        role = spec.role if spec else "researcher"
        ex = self._executors.get(role) or self._executors.get("*")
        if ex is None:
            self.graph.mark(node.task_id, TaskStatus.FAILED, now=self._now(),
                            error=f"no_executor:{role}")
            return

        self.budget.register_agent()
        self.graph.mark(node.task_id, TaskStatus.RUNNING, now=self._now())
        self.graph.heartbeat(node.task_id, now=self._now(), owner=role, lease_s=60)
        self._emit(EventType.AGENT_STARTED, node.task_id, role=role)
        t0 = time.monotonic()
        try:
            res = await ex(node, spec)
        except Exception as e:
            res = ExecResult(ok=False, error=f"exc:{e}")
        finally:
            self.budget.release_agent()

        dur = int((time.monotonic() - t0) * 1000)
        try:
            self.budget.charge(tokens=res.tokens, cost_usd=res.cost, steps=1)
        except Exception:
            self.graph.mark(node.task_id, TaskStatus.BLOCKED, now=self._now(),
                            error="budget_exceeded")
            self._emit(EventType.TASK_FAILED, node.task_id, reason="budget")
            return

        # kanıt kaydı
        for ev in (res.evidence or []):
            e = self.evidence.add(task_id=node.task_id, agent_id=role,
                                  etype=EvidenceType(ev.get("etype", "claim")),
                                  summary=ev.get("summary", ""), detail=ev.get("detail", ""),
                                  reproducible=ev.get("reproducible", False),
                                  user_scope=self.user_scope, project_scope=self.project_scope)
            node.evidence = tuple(node.evidence) + (e.evidence_id,)
            self._emit(EventType.EVIDENCE_ADDED, node.task_id, evidence_id=e.evidence_id)

        self.role_runs.append({"role": role, "agent_id": node.assigned_agent_id,
                               "ok": res.ok, "tokens": res.tokens, "cost": res.cost,
                               "latency_ms": dur, "retry": node.retry_count})

        if not res.ok:
            if node.retry_count < node.max_retry:
                node.retry_count += 1
                self.graph.mark(node.task_id, TaskStatus.PENDING, now=self._now(),
                                error=res.error)
                self._emit(EventType.TASK_FAILED, node.task_id, retry=node.retry_count)
            else:
                self.graph.mark(node.task_id, TaskStatus.FAILED, now=self._now(),
                                error=res.error)
                self._emit(EventType.TASK_FAILED, node.task_id, final=True)
            return

        node.result = res.result
        # checkpoint (durum referansı; ham içerik değil)
        node.checkpoint = {"seq": self._seq, "done_role": role}

        # review gerekiyorsa: reviewer AYRI ajan, kanıtsız PASS yok
        if spec and spec.reviewer_required and self._reviewer is not None:
            self.graph.mark(node.task_id, TaskStatus.WAITING_REVIEW, now=self._now())
            self._emit(EventType.REVIEW_STARTED, node.task_id)
            verdict = await self._reviewer.review(
                task_id=node.task_id, executor_agent_id=node.assigned_agent_id,
                evidence=self.evidence)
            node.reviewer_result = verdict
            if not verdict.get("passed"):
                if node.retry_count < node.max_retry:
                    node.retry_count += 1
                    self.graph.mark(node.task_id, TaskStatus.PENDING, now=self._now(),
                                    error="review_rejected")
                else:
                    self.graph.mark(node.task_id, TaskStatus.FAILED, now=self._now(),
                                    error="review_failed")
                return
        self.graph.mark(node.task_id, TaskStatus.COMPLETED, now=self._now(), result=res.result)
        self._emit(EventType.TASK_COMPLETED, node.task_id)

    # -- ana döngü --
    async def run(self, *, max_rounds: int = 50) -> dict:
        rounds = 0
        while not self.graph.is_complete() and not self._cancelled and rounds < max_rounds:
            rounds += 1
            if self._paused:
                break
            ready = self.graph.ready_tasks()
            if not ready:
                break
            # paralellik sınırı
            cap = max(1, min(self.budget.limits.max_parallel_agents, len(ready)))
            batch = ready[:cap]
            await asyncio.gather(*(self._run_node(n) for n in batch))
        return {
            "complete": self.graph.is_complete(),
            "cancelled": self._cancelled,
            "paused": self._paused,
            "summary": self.graph.summary(),
            "budget": self.budget.status(),
            "rounds": rounds,
        }

    # -- final synthesis: YALNIZ doğrulanmış (completed + kanıtlı) node'lar --
    def synthesize(self) -> dict:
        verified, unverified = [], []
        for n in self.graph.all():
            if n.status == TaskStatus.COMPLETED and self.evidence.has_hard_evidence(n.task_id):
                verified.append(n.task_id)
            elif n.status == TaskStatus.COMPLETED:
                unverified.append(n.task_id)
        return {
            "verified_tasks": verified,
            "excluded_unverified": unverified,   # kanıtsız → final rapora ALINMAZ
            "failed": [n.task_id for n in self.graph.all()
                       if n.status in (TaskStatus.FAILED, TaskStatus.BLOCKED)],
            "conflicts": self.evidence.conflicts(),
        }
