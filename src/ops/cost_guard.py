"""
HAWK Cost Guard — sert token/maliyet tavanları + admin kill switch (F5).

Amaç: multi-agent / provider-fallback yollarında KONTROLSÜZ token/para harcamasını
DURDURMAK. economy_manager yalnızca model tier'ı düşürüyordu (asla bloklamıyordu);
burası GERÇEK sert sınırları uygular ve blocked=True döndürebilir.

Varsayılan limitler SINIRSIZ DEĞİL — güvenli ve düşük. .env ile ayarlanabilir.
Kill switch dosyası kalıcıdır (/data/hawk_memory/KILL_SWITCH) → restart'a dayanır.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

_STATE_DIR = Path(os.getenv("HAWK_MEMORY_DIR", "/data/hawk_memory"))
try:
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    pass
_KILL_FILE = _STATE_DIR / "KILL_SWITCH.json"


def _envf(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)) or default)
    except Exception:
        return default


def _envi(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)) or default)
    except Exception:
        return default


# ---- Güvenli-düşük varsayılan limitler ----
def limits() -> Dict[str, Any]:
    return {
        "max_task_tokens": _envi("HAWK_MAX_TASK_TOKENS", 120_000),   # görev başına toplam token
        "max_task_cost_usd": _envf("HAWK_MAX_TASK_COST_USD", 0.50),  # görev başına $ tavanı
        "daily_hard_usd": _envf("HAWK_DAILY_HARD_USD", 10.0),        # günlük SERT durdurma
        "max_parallel_agents": _envi("HAWK_MAX_PARALLEL_AGENTS", 8),
        "max_total_agents": _envi("HAWK_MAX_TOTAL_AGENTS", 24),
        "max_steps": _envi("HAWK_MAX_STEPS", 12),
        "max_retry": _envi("HAWK_MAX_RETRY", 3),
        "hard_timeout_s": _envi("HAWK_HARD_TIMEOUT_S", 120),
    }


# ---- Kill switch (kalıcı, GRANÜLER) ----
# FAZ 3: global + scope-bazlı ayrı durdurma. Scope'lar:
# Kanonik kill-switch seti (FAZ 1 autonomous-operator). "web" = web_research alias'ı (geriye uyum).
KILL_SCOPES = ("global", "chat", "hawkbase", "model_gateway", "multi_agent", "research",
               "sandbox", "pc_worker", "web", "web_research", "runpod", "training",
               "deployment", "curiosity")


def _scope_alias(scope: str) -> str:
    # web_research ↔ web tek anahtar; ikisinden biri kill ise diğeri de kill sayılır (aşağıda is_killed).
    return {"web_research": "web"}.get(scope, scope)


def _kill_doc() -> Dict[str, Any]:
    try:
        if _KILL_FILE.exists():
            d = json.loads(_KILL_FILE.read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else {}
    except Exception:
        pass
    return {}


def kill_status() -> Dict[str, Any]:
    """Global kill durumu (geriye uyumlu). env HAWK_KILL_SWITCH = global."""
    if os.getenv("HAWK_KILL_SWITCH", "").strip().lower() in ("1", "true", "on", "yes"):
        return {"killed": True, "reason": "env HAWK_KILL_SWITCH", "actor": "env", "ts": 0}
    d = _kill_doc()
    if d.get("killed"):
        return {"killed": True, "reason": d.get("reason", ""), "actor": d.get("actor", ""), "ts": d.get("ts", 0)}
    return {"killed": False, "reason": "", "actor": "", "ts": 0}


def killed_scopes() -> Dict[str, Any]:
    """Aktif scope-kill'ler + global. {'global': bool, 'chat': bool, ...}"""
    d = _kill_doc()
    sc = d.get("scopes", {}) if isinstance(d.get("scopes"), dict) else {}
    out = {"global": bool(kill_status().get("killed"))}
    for s in KILL_SCOPES:
        out[s] = bool(sc.get(s))
    env_scopes = os.getenv("HAWK_KILL_SCOPES", "")
    for s in [x.strip() for x in env_scopes.split(",") if x.strip()]:
        if s in KILL_SCOPES:
            out[s] = True
    return out


def is_killed(scope: str = "") -> bool:
    """scope="" → yalnız global (geriye uyumlu). scope verilirse: global VEYA o scope (veya alias'ı) kapalı mı."""
    if kill_status().get("killed"):
        return True
    if scope:
        ks = killed_scopes()
        # web_research ↔ web: birinin kill'i diğerini de kapsar
        return bool(ks.get(scope) or ks.get(_scope_alias(scope)) or
                    (scope == "web" and ks.get("web_research")))
    return False


def set_kill(on: bool, reason: str = "", actor: str = "admin", scope: str = "global") -> Dict[str, Any]:
    """Kill aç/kapat. scope='global' → tüm sistem; scope=chat|multi_agent|research|pc_worker|web|runpod|training
    → yalnız o yol. Açıkken o yolda YENİ çağrı/görev başlamaz."""
    try:
        d = _kill_doc()
        if scope == "global":
            if on:
                d.update({"killed": True, "reason": reason, "actor": actor, "ts": int(time.time())})
            else:
                d["killed"] = False
        elif scope in KILL_SCOPES:
            sc = d.get("scopes", {}) if isinstance(d.get("scopes"), dict) else {}
            if on:
                sc[scope] = True
            else:
                sc.pop(scope, None)
            d["scopes"] = sc
            d.setdefault("killed", False)
        else:
            return {"ok": False, "error": f"geçersiz scope: {scope}"}
        # dosya: global kapalı + hiç scope yoksa sil (temiz)
        if not d.get("killed") and not (d.get("scopes") or {}):
            if _KILL_FILE.exists():
                _KILL_FILE.unlink()
        else:
            _KILL_FILE.write_text(json.dumps(d), encoding="utf-8")
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}
    return {"ok": True, "global": kill_status(), "scopes": killed_scopes()}


class KillSwitchError(RuntimeError):
    pass


class BudgetExceeded(RuntimeError):
    pass


class TaskBudget:
    """Tek bir görev için token/maliyet sayacı + adım/ajan sayacı. Güvenli iptal
    için her yeni çağrıdan/ajandan ÖNCE .check() çağrılır."""

    def __init__(self, task: str = "", user: str = "", plan: str = "free"):
        L = limits()
        self.task = task
        self.user = user
        self.plan = plan
        self.tokens = 0
        self.cost = 0.0
        self.steps = 0
        self.agents = 0
        self.max_tokens = L["max_task_tokens"]
        self.max_cost = L["max_task_cost_usd"]
        self.max_steps = L["max_steps"]
        self.max_agents = L["max_total_agents"]
        self.started = time.time()
        self.hard_timeout = L["hard_timeout_s"]

    def add(self, tokens: int = 0, cost: float = 0.0):
        self.tokens += max(0, int(tokens or 0))
        self.cost += max(0.0, float(cost or 0.0))

    def check(self, *, new_agent: bool = False) -> None:
        """Sınır aşımında istisna fırlatır (çağıran güvenli durdurur)."""
        if is_killed():
            raise KillSwitchError("kill switch aktif — yeni çağrı başlatılmadı")
        if self.tokens >= self.max_tokens:
            raise BudgetExceeded(f"görev token tavanı aşıldı ({self.tokens}/{self.max_tokens})")
        if self.cost >= self.max_cost:
            raise BudgetExceeded(f"görev maliyet tavanı aşıldı (${self.cost:.4f}/${self.max_cost})")
        if self.steps >= self.max_steps:
            raise BudgetExceeded(f"görev adım tavanı aşıldı ({self.steps}/{self.max_steps})")
        if (time.time() - self.started) > self.hard_timeout:
            raise BudgetExceeded(f"görev süre tavanı aşıldı ({self.hard_timeout}s)")
        if new_agent:
            if self.agents >= self.max_agents:
                raise BudgetExceeded(f"görev ajan tavanı aşıldı ({self.agents}/{self.max_agents})")
            self.agents += 1

    def step(self):
        self.steps += 1


async def daily_hard_blocked() -> Optional[str]:
    """Günlük SERT bütçe aşıldıysa sebep döndürür, yoksa None."""
    try:
        from core import economy_manager as _em
        st = await _em.budget_status()
        if float(st.get("today_spend", 0)) >= limits()["daily_hard_usd"]:
            return (f"günlük sert bütçe aşıldı "
                    f"(${st.get('today_spend')}/${limits()['daily_hard_usd']})")
    except Exception:
        pass
    return None


async def preflight(*, allow_fallback: bool = True) -> Dict[str, Any]:
    """Yeni agent/provider(fallback) çağrısından ÖNCE çağrılır.
    Döner: {blocked: bool, reason: str}."""
    ks = kill_status()
    if ks["killed"]:
        return {"blocked": True, "reason": f"kill switch: {ks.get('reason') or 'aktif'}"}
    hard = await daily_hard_blocked()
    if hard:
        return {"blocked": True, "reason": hard}
    return {"blocked": False, "reason": "ok"}
