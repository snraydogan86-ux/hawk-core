# HAWK Open Benchmark

73 deterministic tests used to evaluate every version of **HAWK Base** before it can reach users.
Same prompts, same greedy decoding (temperature 0, fixed seed) for every candidate — objective, reproducible, no cherry-picking.

## Distribution
- 15 Turkish natural conversation
- 10 English natural
- 5 structured JSON + 5 tool-calling
- 10 reasoning
- 10 code
- 5 memory + safety
- (+ extended: base instruction-following, prompt-injection, forget)

## Run
```bash
python run_bench.py --run --endpoint <chat-completions-url> --model hawk-base --key hawk-base-vX
```
Scoring is deterministic (`score.py`): JSON/tool/regex/language/number/refusal checks. Results are written to `results_<key>.json`.
