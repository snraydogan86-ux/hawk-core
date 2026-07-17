"""
Agent registry — zorunlu roller + göreve göre dinamik uzman ajan üretimi.

Denetim bulgusu: bugün 4 ayrı registry var (agent_loop AGENT_PROFILES=6,
hawk_agents personas=16, hawk_core AGENTS=14, agent_router intent map). Bu modül
TEK merkezî registry'dir; eskiler backward-compatible adapter ile beslenecek
(bu turda eskiler SİLİNMEZ, yalnız yeni merkez kurulur).

Dinamik ajan güvenlik/maliyet zarfını (DynamicAgentSpec + validate_spec) geçmeden
kaydedilemez; reviewer executor ile aynı instance olamaz.
"""
from __future__ import annotations

from dataclasses import dataclass

from .agent_spec import (
    DynamicAgentSpec, SecurityLevel, MANDATORY_ROLES, REVIEWER_ROLES, validate_spec,
)
from .policy import AgentLimits, DEFAULT_LIMITS


@dataclass(frozen=True)
class RoleTemplate:
    role: str
    model_role: str           # ModelGateway rolü
    default_tools: tuple[str, ...]
    reviewer_required: bool = False
    security_level: SecurityLevel = SecurityLevel.STANDARD


# Zorunlu rollerin varsayılan şablonları (Section 4).
ROLE_TEMPLATES: dict[str, RoleTemplate] = {
    "supervisor":        RoleTemplate("supervisor", "reasoning", (), False, SecurityLevel.STANDARD),
    "planner":           RoleTemplate("planner", "reasoning", (), False, SecurityLevel.LOW),
    "researcher":        RoleTemplate("researcher", "base", ("web_search", "web_fetch", "read_file", "grep"), False, SecurityLevel.LOW),
    "coder":             RoleTemplate("coder", "code", ("read_file", "grep", "list_files", "write_file", "edit_file", "apply_patch"), True, SecurityLevel.ELEVATED),
    "tester":            RoleTemplate("tester", "code", ("run_tests", "build", "read_file"), False, SecurityLevel.STANDARD),
    "security_reviewer": RoleTemplate("security_reviewer", "reviewer", ("read_file", "grep", "git_diff"), False, SecurityLevel.LOW),
    "cost_reviewer":     RoleTemplate("cost_reviewer", "reviewer", (), False, SecurityLevel.LOW),
    "fact_checker":      RoleTemplate("fact_checker", "reviewer", ("web_search", "web_fetch", "read_file"), False, SecurityLevel.LOW),
    "uiux_reviewer":     RoleTemplate("uiux_reviewer", "reviewer", ("read_file",), False, SecurityLevel.LOW),
    "documentation":     RoleTemplate("documentation", "base", ("read_file", "write_file"), False, SecurityLevel.STANDARD),
    "evidence_collector": RoleTemplate("evidence_collector", "mini", ("read_file", "grep", "list_files"), False, SecurityLevel.LOW),
    "final_synthesizer": RoleTemplate("final_synthesizer", "reasoning", (), False, SecurityLevel.LOW),
}


class RegistryError(Exception):
    pass


class AgentRegistry:
    def __init__(self, limits: AgentLimits | None = None):
        self.limits = limits or DEFAULT_LIMITS
        self._specs: dict[str, DynamicAgentSpec] = {}

    # -- zorunlu roller --
    @staticmethod
    def mandatory_roles() -> frozenset[str]:
        return MANDATORY_ROLES

    def template(self, role: str) -> RoleTemplate | None:
        return ROLE_TEMPLATES.get(role)

    # -- dinamik kayıt --
    def register(self, spec: DynamicAgentSpec) -> DynamicAgentSpec:
        errs = validate_spec(spec, self.limits)
        if errs:
            raise RegistryError("; ".join(errs))
        if spec.agent_id in self._specs:
            raise RegistryError(f"agent_id zaten kayıtlı: {spec.agent_id}")
        self._specs[spec.agent_id] = spec
        return spec

    def build_from_role(self, *, agent_id: str, role: str, objective: str,
                        user_scope: str, project_scope: str = "",
                        name: str | None = None, **overrides) -> DynamicAgentSpec:
        """Zorunlu rol şablonundan güvenli varsayılanlarla spec üretir."""
        tpl = ROLE_TEMPLATES.get(role)
        if tpl is None and role not in MANDATORY_ROLES:
            # özel rol: şablon yok, güvenli minimum ile devam (executor değilse düşük yetki)
            tpl = RoleTemplate(role, overrides.get("model_role", "base"),
                               tuple(overrides.get("allowed_tools", ())),
                               overrides.get("reviewer_required", False),
                               SecurityLevel.LOW)
        spec = DynamicAgentSpec(
            agent_id=agent_id, name=name or role, role=role, objective=objective,
            task_scope=overrides.get("task_scope", objective[:64] or role),
            user_scope=user_scope, project_scope=project_scope,
            allowed_tools=tuple(overrides.get("allowed_tools", tpl.default_tools)),
            model_role=overrides.get("model_role", tpl.model_role),
            reviewer_required=overrides.get("reviewer_required", tpl.reviewer_required),
            security_level=overrides.get("security_level", tpl.security_level),
            token_budget=overrides.get("token_budget", 20_000),
            cost_budget_usd=overrides.get("cost_budget_usd", 0.10),
            max_steps=overrides.get("max_steps", 8),
            spawn_depth=overrides.get("spawn_depth", 0),
        )
        return self.register(spec)

    def get(self, agent_id: str) -> DynamicAgentSpec:
        return self._specs[agent_id]

    def list(self) -> list[DynamicAgentSpec]:
        return list(self._specs.values())

    # -- executor/reviewer ayrımı (Section 6) --
    def assert_independent_reviewer(self, executor_id: str, reviewer_id: str) -> None:
        if executor_id == reviewer_id:
            raise RegistryError("reviewer executor ile aynı instance olamaz")
        ex = self._specs.get(executor_id)
        rv = self._specs.get(reviewer_id)
        if rv is None:
            raise RegistryError("reviewer kayıtlı değil")
        if rv.role not in REVIEWER_ROLES:
            raise RegistryError(f"reviewer rolü değil: {rv.role}")
        # farklı model/provider reviewer tercih edilir (zorunlu değil ama önerilir)
        if ex is not None and ex.user_scope != rv.user_scope:
            raise RegistryError("reviewer farklı kullanıcı scope'unda olamaz")
