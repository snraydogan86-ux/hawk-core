# HAWK — Core Modules

This directory contains real modules from the HAWK platform, published for transparency —
so you can read HAWK's own engineering, not just its description. These are the genuine
building blocks behind the architecture in [`../docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md).

> Note: these modules are extracted from the larger HAWK codebase for review. They reference
> internal services and are meant to be *read* as evidence of real engineering, not installed
> as a standalone package.

| Path | What it is |
|------|------------|
| `safety/safety_crisis.py` | Deterministic, model-independent crisis / self-harm safety layer. Detects intent, responds safely, validates output — never leaks methods, never fabricates helplines. |
| `safety/prompt_shield.py` | Prompt-injection / jailbreak shield. |
| `memory/memory_store.py` | Persistent, per-user memory — facts + history, strictly isolated per user. |
| `memory/memory_pipeline.py` | The single write path for facts (safe extraction, no secrets). |
| `model_family/model_registry.py` | The HAWK Base version state machine (draft → shadow → canary → production) with SHA-256 provenance. |
| `model_family/promotion_controller.py` | Evidence-gated promotion: a version cannot reach users without passing benchmark + safety gates. |
| `model_family/hawk_base_versions.py` | The version lineage and checksums. |
| `ops/cost_guard.py` | Kill-switches (granular scopes) + daily budget guard. |
| `serving/hawk_lora_server.py` | The lightweight adapter server that serves HAWK Base (foundation + LoRA). |

Together these show what makes HAWK a real system: its own model with disciplined ML-ops
(versioning, gates, provenance, rollback), a real safety layer, real per-user memory, and
operational guardrails.
