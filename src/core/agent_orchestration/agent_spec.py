"""
DynamicAgentSpec — göreve göre üretilen geçici uzman ajanın güvenli tanımı.

Dinamik ajan = yeni Python kodu üretip sınırsız çalıştırmak DEĞİLDİR.
Yalnız doğrulanmış runtime içinde, sandbox'lı, policy-kontrollü geçici işçidir.
Her spec bir güvenlik ve maliyet zarfı taşır; ajan bu zarfı KENDİ yükseltemez.

Bu modül saf veridir (I/O yok, ağ yok, DB yok) — deterministik test edilir.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any


class SecurityLevel(str, Enum):
    LOW = "low"          # salt-okuma / analiz
    STANDARD = "standard"
    ELEVATED = "elevated"  # yazma/patch, reviewer zorunlu
    CRITICAL = "critical"  # shell/deploy sınıfı, insan onayı zorunlu


# Zorunlu (yerleşik) roller — registry ile paylaşılır.
MANDATORY_ROLES = frozenset({
    "supervisor", "planner", "researcher", "coder", "tester",
    "security_reviewer", "cost_reviewer", "fact_checker", "uiux_reviewer",
    "documentation", "evidence_collector", "final_synthesizer",
})

# Reviewer sınıfı roller — executor ile aynı instance olamaz.
REVIEWER_ROLES = frozenset({
    "security_reviewer", "cost_reviewer", "fact_checker",
    "uiux_reviewer", "reviewer",
})

# Bir ajana verilebilecek TÜM araçların üst-kümesi (global allowlist).
# Gerçek çalıştırma anında tool_engine ile kesişim alınır; buradaki liste
# yalnız spec doğrulamasının üst sınırıdır.
GLOBAL_TOOL_UNIVERSE = frozenset({
    "web_search", "web_fetch", "read_file", "list_files", "grep",
    "run_tests", "build", "write_file", "edit_file", "apply_patch",
    "run_shell", "git_diff", "git_status", "sql_read", "ask_ai",
    "memory_read", "memory_write", "device_command", "http_get",
})

# Hiçbir spec'in izin veremeyeceği eylemler (varsayılan yasak).
ALWAYS_FORBIDDEN = frozenset({
    "git_push", "force_push", "production_deploy", "production_restart",
    "db_drop", "db_truncate", "delete_user_data", "rotate_secret",
    "start_paid_gpu", "promote_model", "disable_guardian",
    "raise_own_limits", "spawn_beyond_depth",
})

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]{2,63}$")


@dataclass
class DynamicAgentSpec:
    agent_id: str
    name: str
    role: str
    objective: str
    task_scope: str                     # bu ajanın dokunabileceği görev alanı
    user_scope: str                     # kimlik (hash'lenerek saklanır)
    project_scope: str = ""             # proje izolasyonu ("" = kişisel)
    allowed_tools: tuple[str, ...] = ()
    forbidden_actions: tuple[str, ...] = ()
    model_role: str = "base"            # hawk_mini/base/code/reasoning/vision/external/reviewer
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    success_criteria: tuple[str, ...] = ()
    evidence_requirements: tuple[str, ...] = ()
    token_budget: int = 20_000
    cost_budget_usd: float = 0.10
    max_steps: int = 8
    max_tool_calls: int = 16
    max_retries: int = 2
    timeout_seconds: int = 120
    spawn_depth: int = 0                # bu ajanın ağaçtaki derinliği
    reviewer_required: bool = False
    security_level: SecurityLevel = SecurityLevel.STANDARD

    # -- türetilmiş güvenli alanlar --
    @property
    def user_scope_hash(self) -> str:
        return hashlib.sha256(("u:" + self.user_scope).encode()).hexdigest()[:16]

    @property
    def project_scope_hash(self) -> str:
        if not self.project_scope:
            return ""
        return hashlib.sha256(("p:" + self.project_scope).encode()).hexdigest()[:16]

    def is_reviewer(self) -> bool:
        return self.role in REVIEWER_ROLES

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["security_level"] = self.security_level.value
        return d


def validate_spec(spec: DynamicAgentSpec, limits: "AgentLimits | None" = None) -> list[str]:
    """Spec'i güvenlik zarfına göre doğrular. Boş liste = geçerli.

    limits verilirse (policy.AgentLimits), bütçe/derinlik tavanları da denetlenir.
    """
    errs: list[str] = []

    if not _ID_RE.match(spec.agent_id or ""):
        errs.append(f"agent_id geçersiz: {spec.agent_id!r}")
    if not (spec.name and spec.role and spec.objective):
        errs.append("name/role/objective zorunlu")
    if not spec.task_scope:
        errs.append("task_scope zorunlu")
    if not spec.user_scope:
        errs.append("user_scope zorunlu (kimlik izolasyonu)")

    # Araç allowlist: global evrenin dışına çıkamaz.
    bad_tools = [t for t in spec.allowed_tools if t not in GLOBAL_TOOL_UNIVERSE]
    if bad_tools:
        errs.append(f"bilinmeyen/izinsiz araç: {bad_tools}")

    # Yasak eylemler her zaman yasaktır; allowlist ile çakışamaz.
    leaked = [t for t in spec.allowed_tools if t in ALWAYS_FORBIDDEN]
    if leaked:
        errs.append(f"kesin-yasak eylem allowlist'te: {leaked}")
    # forbidden_actions ALWAYS_FORBIDDEN'i daraltamaz (yalnız genişletebilir).

    # Bütçe/sayaç alanları pozitif olmalı.
    for fld in ("token_budget", "max_steps", "max_tool_calls", "timeout_seconds"):
        if getattr(spec, fld) <= 0:
            errs.append(f"{fld} pozitif olmalı")
    if spec.cost_budget_usd <= 0:
        errs.append("cost_budget_usd pozitif olmalı")
    if spec.max_retries < 0:
        errs.append("max_retries negatif olamaz")
    if spec.spawn_depth < 0:
        errs.append("spawn_depth negatif olamaz")

    # Kritik güvenlik seviyesi reviewer ZORUNLU kılar.
    if spec.security_level == SecurityLevel.CRITICAL and not spec.reviewer_required:
        errs.append("security_level=critical için reviewer_required=True olmalı")
    if spec.security_level == SecurityLevel.ELEVATED and not spec.reviewer_required:
        errs.append("security_level=elevated için reviewer_required=True olmalı")

    # Reviewer rolü yazma/shell aracı taşıyamaz (bağımsız denetçi kalmalı).
    if spec.is_reviewer():
        write_tools = {"write_file", "edit_file", "apply_patch", "run_shell", "device_command"}
        bad = [t for t in spec.allowed_tools if t in write_tools]
        if bad:
            errs.append(f"reviewer rolü yazma/shell aracı taşıyamaz: {bad}")

    if limits is not None:
        if spec.token_budget > limits.max_agent_tokens:
            errs.append(f"token_budget tavanı aşıyor ({spec.token_budget}>{limits.max_agent_tokens})")
        if spec.cost_budget_usd > limits.max_agent_cost_usd:
            errs.append(f"cost_budget tavanı aşıyor")
        if spec.max_steps > limits.max_agent_steps:
            errs.append(f"max_steps tavanı aşıyor")
        if spec.spawn_depth > limits.max_spawn_depth:
            errs.append(f"spawn_depth tavanı aşıyor ({spec.spawn_depth}>{limits.max_spawn_depth})")

    return errs
