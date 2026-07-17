"""
Policy — ajan orkestrasyonunun güvenlik ve maliyet zarfı.

İki şey sağlar:
  1) AgentLimits — sabit tavanlar (env-tunable). Ajan bunları KENDİ yükseltemez.
  2) BudgetTree — bir görev-ağacı boyunca token/maliyet/ajan/derinlik AGGREGASYONU.
     Mevcut cost_guard yalnız per-call max_steps'i uyguluyordu; burada eksik olan
     ağaç-genelinde toplam bütçe muhasebesini ekliyoruz (denetimde DEAD çıkan
     TaskBudget'in gerçek, kullanılan hâli).

Kill-switch mevcut cost_guard'tan okunur (varsa); yeni bir switch icat edilmez.
Onay politikası (Section 10) burada tek merkezde tanımlıdır.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from .agent_spec import ALWAYS_FORBIDDEN


def _envf(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except Exception:
        return default


def _envi(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except Exception:
        return default


@dataclass(frozen=True)
class AgentLimits:
    # ağaç (görev) tavanları
    max_parallel_agents: int = field(default_factory=lambda: _envi("HAWK_MAX_PARALLEL_AGENTS", 8))
    max_total_agents: int = field(default_factory=lambda: _envi("HAWK_MAX_TOTAL_AGENTS", 24))
    max_spawn_depth: int = field(default_factory=lambda: _envi("HAWK_MAX_SPAWN_DEPTH", 3))
    max_task_steps: int = field(default_factory=lambda: _envi("HAWK_MAX_TASK_STEPS", 60))
    max_task_tokens: int = field(default_factory=lambda: _envi("HAWK_MAX_TASK_TOKENS", 400_000))
    max_task_cost_usd: float = field(default_factory=lambda: _envf("HAWK_MAX_TASK_COST", 2.0))
    hard_timeout_s: int = field(default_factory=lambda: _envi("HAWK_HARD_TIMEOUT", 900))
    # tek-ajan tavanları
    max_agent_tokens: int = field(default_factory=lambda: _envi("HAWK_MAX_AGENT_TOKENS", 120_000))
    max_agent_cost_usd: float = field(default_factory=lambda: _envf("HAWK_MAX_AGENT_COST", 0.50))
    max_agent_steps: int = field(default_factory=lambda: _envi("HAWK_MAX_AGENT_STEPS", 12))
    max_retry: int = field(default_factory=lambda: _envi("HAWK_MAX_RETRY", 3))


DEFAULT_LIMITS = AgentLimits()


class BudgetExceeded(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def _kill_active() -> tuple[bool, str]:
    """Mevcut cost_guard kill-switch'ini okur; yoksa güvenli varsayılan (kapalı)."""
    try:
        from core import cost_guard  # type: ignore
        if cost_guard.is_killed():
            return True, cost_guard.kill_status().get("reason", "kill_switch")
    except Exception:
        pass
    if os.getenv("HAWK_KILL_SWITCH", "").lower() in ("1", "true", "yes"):
        return True, "env_kill_switch"
    return False, ""


class BudgetTree:
    """Bir görev-ağacı boyunca paylaşılan bütçe muhasebesi.

    Tüm alt-ajanlar AYNI BudgetTree'yi paylaşır; böylece toplam token/maliyet/
    ajan sayısı ağaç genelinde toplanır. Ajan kendi limitini yükseltemez —
    yalnız charge()/register_agent() ile tüketir.
    """

    def __init__(self, limits: AgentLimits | None = None):
        self.limits = limits or DEFAULT_LIMITS
        self.tokens_used = 0
        self.cost_used = 0.0
        self.steps_used = 0
        self.total_agents = 0
        self.active_agents = 0
        self.retries_used = 0
        self.blocked = False
        self.block_reason = ""

    # -- durum --
    def _block(self, reason: str) -> None:
        self.blocked = True
        self.block_reason = reason

    def status(self) -> dict:
        return {
            "blocked": self.blocked, "reason": self.block_reason,
            "tokens_used": self.tokens_used, "cost_used": round(self.cost_used, 4),
            "steps_used": self.steps_used, "total_agents": self.total_agents,
            "active_agents": self.active_agents,
            "limits": {
                "max_task_tokens": self.limits.max_task_tokens,
                "max_task_cost_usd": self.limits.max_task_cost_usd,
                "max_total_agents": self.limits.max_total_agents,
                "max_parallel_agents": self.limits.max_parallel_agents,
            },
        }

    # -- kontrol (yeni iş başlamadan ÖNCE çağrılır) --
    def can_spawn(self, depth: int, *, parallel: bool = True) -> tuple[bool, str]:
        killed, reason = _kill_active()
        if killed:
            self._block(f"kill_switch:{reason}")
            return False, self.block_reason
        if self.blocked:
            return False, self.block_reason
        if depth > self.limits.max_spawn_depth:
            return False, f"spawn_depth>{self.limits.max_spawn_depth}"
        if self.total_agents + 1 > self.limits.max_total_agents:
            return False, f"max_total_agents={self.limits.max_total_agents}"
        if parallel and self.active_agents + 1 > self.limits.max_parallel_agents:
            return False, f"max_parallel_agents={self.limits.max_parallel_agents}"
        if self.tokens_used >= self.limits.max_task_tokens:
            self._block("max_task_tokens")
            return False, self.block_reason
        if self.cost_used >= self.limits.max_task_cost_usd:
            self._block("max_task_cost")
            return False, self.block_reason
        return True, ""

    def register_agent(self) -> None:
        self.total_agents += 1
        self.active_agents += 1

    def release_agent(self) -> None:
        self.active_agents = max(0, self.active_agents - 1)

    def charge(self, *, tokens: int = 0, cost_usd: float = 0.0, steps: int = 0) -> None:
        """Tüketimi işler; tavan aşılırsa ağacı bloklar (BudgetExceeded)."""
        self.tokens_used += max(0, tokens)
        self.cost_used += max(0.0, cost_usd)
        self.steps_used += max(0, steps)
        if self.tokens_used > self.limits.max_task_tokens:
            self._block("max_task_tokens")
            raise BudgetExceeded(self.block_reason)
        if self.cost_used > self.limits.max_task_cost_usd:
            self._block("max_task_cost")
            raise BudgetExceeded(self.block_reason)
        if self.steps_used > self.limits.max_task_steps:
            self._block("max_task_steps")
            raise BudgetExceeded(self.block_reason)

    def note_retry(self) -> tuple[bool, str]:
        if self.retries_used + 1 > self.limits.max_retry:
            return False, f"max_retry={self.limits.max_retry}"
        self.retries_used += 1
        return True, ""


# ---- Onay politikası (Section 10) --------------------------------------------

# Açık insan onayı olmadan YAPILMAYACAK eylemler.
APPROVAL_REQUIRED = frozenset({
    "git_push", "force_push", "production_deploy", "production_restart",
    "db_drop", "db_delete", "db_truncate", "delete_user_data",
    "rotate_secret", "start_paid_service", "start_paid_gpu",
    "promote_model", "irreversible_data_change",
})

# Onay gerektirmeyen güvenli işlemler (gereksiz onay sorma).
ALWAYS_SAFE = frozenset({
    "read_code", "local_patch", "unit_test", "syntax_check",
    "safe_fixture", "prepare_diff", "analysis", "dry_run",
    "web_search", "read_file", "grep", "list_files",
})


def needs_approval(action: str) -> bool:
    a = (action or "").strip().lower()
    if a in ALWAYS_SAFE:
        return False
    if a in APPROVAL_REQUIRED or a in ALWAYS_FORBIDDEN:
        return True
    return False
