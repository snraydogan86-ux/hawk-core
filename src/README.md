# HAWK — Source

Real modules from the HAWK platform, published for transparency. This is HAWK's own
engineering — the systems behind the [architecture](../docs/ARCHITECTURE.md).

> These modules are extracted from the larger HAWK codebase for review. They reference internal
> services and are meant to be **read** as evidence of real engineering, not installed as a
> standalone package. Third-party integrations (messaging, payments, specific cloud vendors) are
> intentionally excluded from the open core.

## Layout

| Package | Modules | What lives here |
|---------|--------:|-----------------|
| `core/` | 69 | The platform core — brain/reasoning, conversation engine, memory, safety, cost/kill-switch guardrails, autonomous operator, continuous learning, economy, and more. |
| `core/model_family/` | 39 | **HAWK's ML-ops.** The model version state machine (draft → shadow → canary → production), evidence-gated promotion, SHA-256 provenance, benchmarking, and the version lineage — how every HAWK model is trained, checked, and shipped safely. |
| `core/agent_orchestration/` | 12 | Multi-agent orchestration — decompose a goal, run specialized agents, review, synthesize. |
| `core/hawk_core/` | 13 | Device pairing, workspace, and self-directed improvement primitives. |
| `serving/` | 1 | The lightweight adapter server that serves HAWK Base (foundation + LoRA). |

## Highlights worth reading

- **`core/safety_crisis.py`** — deterministic, model-independent crisis / self-harm safety layer.
- **`core/prompt_shield.py`** — prompt-injection / jailbreak shield.
- **`core/memory_store.py`** + **`core/memory_pipeline.py`** — persistent, strictly per-user memory.
- **`core/model_family/model_registry.py`** + **`promotion_controller.py`** — the state machine + gates that prevent any unproven model from reaching users.
- **`core/cost_guard.py`** — granular kill-switches + daily budget guard.
- **`serving/hawk_lora_server.py`** — how HAWK Base is served (foundation model + LoRA adapter).

Together: HAWK's own model, disciplined ML-ops, a real safety stack, real memory, and
operational guardrails — the substance behind "a real AI, not a wrapper."
