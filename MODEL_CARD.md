# HAWK Base — Model Card

**HAWK Base** is the model that powers HAWK. It is fine-tuned and served by us, on our own infrastructure.

## Summary

| Field | Value |
|---|---|
| Model name | HAWK Base |
| Foundation model | Qwen3-8B (open-weight, Apache-2.0) |
| Fine-tuning method | QLoRA — 4-bit nf4, double-quant, bf16 compute |
| LoRA config | rank 16, alpha 32, dropout 0.05, all attention + MLP projections |
| Objective | Supervised fine-tuning, prompt-masked (loss only on assistant tokens) |
| Context length | 1024 (training) |
| Supported languages | Turkish, English, German, French, Spanish, Arabic, Russian |
| Owner | Soner Aydoğan |

We train a full version lineage (v0.1 → current), each evaluated on the same open benchmark before any promotion. Nothing reaches users without passing an evidence gate (shadow → canary → production).

## What HAWK Base does well

Measured on the HAWK open benchmark (73 deterministic tests, temperature 0, fixed seed — see [`eval/`](eval/)):

| Category | Score |
|---|---|
| Reasoning | 10 / 10 |
| Code | 10 / 10 |
| Tool-calling | 5 / 5 |
| Structured JSON | 5 / 5 |
| Prompt-injection resistance | 5 / 5 |
| Instruction-following (base) | 5 / 5 |
| Memory + safety | 5 / 5 |
| English natural | 9 / 10 |
| Turkish natural | 9 / 15 |
| **Total** | **~65 / 73** |

Honest note: an 8B model is excellent at everyday conversation, reasoning, code and tool use, but has a ceiling on the hardest, most comprehensive tasks. HAWK routes those to stronger models automatically, so the user always gets the best answer — while the vast majority of traffic stays on our own HAWK Base. Turkish naturalness and broader multilingual depth are our active improvement targets, addressed each version with more and better training data.

## How it is trained

The full pipeline is in [`training/train_hawk_base_lora.py`](training/train_hawk_base_lora.py). In short:

1. Curated SFT dataset in the standard chat `messages` format (system / user / assistant), covering natural conversation (multilingual), identity, tool-calling, reasoning, code, memory, and safety.
2. QLoRA fine-tune on the open foundation model, prompt-masked so the model learns to *respond*, not to parrot prompts.
3. The resulting adapter is versioned, checksummed (SHA-256), evaluated on the open benchmark, and only then considered for promotion.

A sample of the training data format is in [`training/data_sample.jsonl`](training/data_sample.jsonl).

## Serving

HAWK Base is served on our own GPU via a lightweight adapter server (base model + LoRA adapter). It handles normal conversation for real users today, with automatic fall-through to stronger models for the hardest tasks and automatic recovery if a node fails.

## Responsible use

HAWK Base is aligned to refuse harmful requests, resist prompt injection, never expose personal/secret data, and handle crisis/self-harm situations with a dedicated safety layer. Safety behavior is verified every version.

---

*HAWK Base is built and owned by Soner Aydoğan. Fine-tuned on Qwen3-8B (Apache-2.0).*
