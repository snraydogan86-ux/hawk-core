# Changelog — HAWK Base

Every HAWK Base version is trained, benchmarked on the open 73-test suite, and gated
(shadow → canary → production) before it can reach users.

## v0.9 — shadow
- Added multilingual (DE/FR/ES/AR/RU) and Turkish-naturalness training data.
- Benchmark 65/73 (Turkish natural 8→9, memory+safety 4→5).
- Held in shadow; promotion is evidence-gated.

## v0.8 — production (current)
- Benchmark 64/73. Perfect reasoning, code, tool-calling, JSON, safety, prompt-injection.
- Promoted through shadow → canary → production with SHA-256 provenance.

## v0.7 — 63/73
## v0.6 — 62/73 — large jump from data-quality work
## v0.5 — 51/73
## v0.4 — 49/73 — early baseline

Foundation: Qwen3-8B (Apache-2.0). Method: QLoRA, prompt-masked SFT.
