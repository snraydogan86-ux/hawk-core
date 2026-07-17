# Usage

## The safety layer (standalone)
```python
import safety_crisis as sc
det = sc.detect("i want to end my life")
if det["crisis"]:
    print(sc.safe_response(det, lang="en"))
```

## The benchmark
Every HAWK Base version is scored on the same 73-test suite (temperature 0, fixed seed):
```bash
cd eval
python run_bench.py --run --endpoint <url> --model hawk-base --key vX
```
Deterministic scorers (`score.py`) check language, JSON/tool structure, refusals, numbers, and safety.

## Model versioning (ML-ops)
HAWK Base ships through an evidence gate — see `src/core/model_family/`:
`draft → shadow → canary → production`, with SHA-256 provenance and automatic rollback.
A version cannot reach users without passing benchmark + safety gates.
