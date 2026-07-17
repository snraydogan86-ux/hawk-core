# Examples

## Safety layer
```bash
python examples/safety_demo.py
```
Shows how HAWK detects crisis/self-harm intent and returns a deterministic, safe response
(no methods, no fabricated helplines, minor-aware) — independent of the model.

## Benchmark
```bash
cd eval
python run_bench.py --self-test        # verify scorers (no GPU/network)
python run_bench.py --run --endpoint <chat-completions-url> --model hawk-base --key my-run
```
See [`../eval/RESULTS.md`](../eval/RESULTS.md) for published scores per version.
