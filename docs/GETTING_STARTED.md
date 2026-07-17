# Getting Started

HAWK-core is the open core of HAWK — the model (HAWK Base), its training pipeline, the open
benchmark, the architecture, and HAWK's own engineering.

## Read the story
1. [`README.md`](../README.md) — what HAWK is and why it's a real AI, not a wrapper.
2. [`MODEL_CARD.md`](../MODEL_CARD.md) — HAWK Base: foundation, method, benchmark scores.
3. [`docs/ARCHITECTURE.md`](ARCHITECTURE.md) — the agent OS.
4. [`src/README.md`](../src/README.md) — the real code map.

## Try it
```bash
python examples/safety_demo.py          # the safety layer, no setup
cd eval && python run_bench.py --self-test   # the benchmark scorers
```

## Train HAWK Base
See [`training/`](../training/) — a reproducible QLoRA fine-tune on an open foundation model.
```bash
pip install -r training/requirements.txt
python training/train_hawk_base_lora.py --base <foundation> --train data.jsonl --val val.jsonl --qlora --epochs 3
```
