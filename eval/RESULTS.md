# HAWK Base — Benchmark Results

All scores on the same 73-test open benchmark (temperature 0, fixed seed). Every version is
evaluated identically before it can be considered for promotion. No cherry-picking.

| Version | Total | Notes |
|---------|-------|-------|
| v0.4    | 49 / 73 | early baseline |
| v0.5    | 51 / 73 | |
| v0.6    | 62 / 73 | large jump (data quality) |
| v0.7    | 63 / 73 | |
| **v0.8**| **64 / 73** | **current production** |
| v0.9    | 65 / 73 | shadow (multilingual + Turkish-naturalness data) |

### v0.8 (production) — per category

| Category | Score |
|----------|-------|
| Reasoning | 10 / 10 |
| Code | 10 / 10 |
| Tool-calling | 5 / 5 |
| Structured JSON | 5 / 5 |
| Prompt-injection resistance | 5 / 5 |
| Instruction-following | 5 / 5 |
| Memory + safety | 5 / 5 |
| English natural | 9 / 10 |
| Turkish natural | 8 / 15 |

Turkish naturalness and broader multilingual depth are the active improvement targets — addressed
each version with more and better training data. Promotion follows an evidence gate:
**shadow → canary → production**, with automatic rollback on regression.
