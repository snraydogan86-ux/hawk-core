# Contributing to HAWK

Thanks for your interest in HAWK. This repository is the open core of HAWK — the model
(HAWK Base), its training pipeline, the open benchmark, and the architecture.

## Ways to contribute
- **Benchmark:** propose new test cases (`eval/testset.jsonl`) or better deterministic scorers (`eval/score.py`).
- **Training data:** high-quality, multilingual, natural-conversation examples in the `messages` format (see `training/data_sample.jsonl`).
- **Docs:** clarity and accuracy improvements.
- **Bugs / ideas:** open an issue.

## Ground rules
- Keep it honest. Benchmark numbers are reproducible; never cherry-pick.
- No personal or secret data in examples or issues.
- Be respectful (see `CODE_OF_CONDUCT.md`).

## Pull requests
1. Fork, branch, make focused changes.
2. Explain *what* and *why* in the PR description.
3. If you touch the benchmark or training, include before/after numbers.

HAWK is built and owned by Soner Aydoğan.
