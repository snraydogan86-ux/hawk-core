#!/usr/bin/env python3
"""
HAWK Base — LoRA/QLoRA SFT eğitici (GPU KUTUSU). trl'siz: transformers.Trainer + peft.

v0.2 FORMAT FIX — PROMPT MASKELEME: loss YALNIZ asistan cevabına uygulanır
(system+user promptu labels=-100 ile maskelenir). Böylece model sistem-promptunu
EZBERLEMEZ/tekrar ETMEZ → 2. şahıs sızıntısı ve parça-çıktı sorunu çözülür.

Girdi: compile_sft.py çıktısı ('messages' alanı: system/user/assistant).
Çıktı: LoRA adapter (base DEĞİŞMEZ).
"""
from __future__ import annotations
import argparse, json, os, sys


def _load_examples(path: str) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            msgs = d.get("messages")
            if msgs and len(msgs) >= 2:
                rows.append({"messages": msgs})
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="Qwen/Qwen3-8B")
    ap.add_argument("--train", required=True)
    ap.add_argument("--val", default="")
    ap.add_argument("--out", required=True)
    ap.add_argument("--qlora", action="store_true")
    ap.add_argument("--lora-r", type=int, default=16)
    ap.add_argument("--lora-alpha", type=int, default=32)
    ap.add_argument("--lora-dropout", type=float, default=0.05)
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--epochs", type=float, default=3.0)
    ap.add_argument("--batch", type=int, default=1)
    ap.add_argument("--grad-accum", type=int, default=16)
    ap.add_argument("--seq-len", type=int, default=1024)
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args()

    try:
        import torch
        from datasets import Dataset
        from transformers import (AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig,
                                   DataCollatorForSeq2Seq, Trainer, TrainingArguments, set_seed)
        from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    except Exception as e:  # pragma: no cover
        print(f"[train] ML bağımlılıkları eksik ({e}). GPU kutusunda requirements-train.txt ile koşar.",
              file=sys.stderr)
        return 3

    set_seed(a.seed)
    print(f"[train] base={a.base} qlora={a.qlora} epochs={a.epochs} seq={a.seq_len} (PROMPT-MASKED)", flush=True)

    tok = AutoTokenizer.from_pretrained(a.base, trust_remote_code=True)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token

    def _tok_mask(ex):
        msgs = ex["messages"]
        # prompt = system+user + generation marker; full = prompt + asistan cevabı
        # enable_thinking=False: Qwen3 düşünme-modunu KAPAT -> model boş <think></think> üretmez
        prompt_ids = tok.apply_chat_template(msgs[:-1], add_generation_prompt=True, tokenize=True,
                                             enable_thinking=False)
        full_ids = tok.apply_chat_template(msgs, add_generation_prompt=False, tokenize=True,
                                           enable_thinking=False)
        labels = [-100] * len(prompt_ids) + list(full_ids[len(prompt_ids):])
        full_ids = full_ids[:a.seq_len]
        labels = labels[:a.seq_len]
        return {"input_ids": full_ids, "attention_mask": [1] * len(full_ids), "labels": labels}

    quant = None
    if a.qlora:
        quant = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                                   bnb_4bit_compute_dtype=torch.bfloat16, bnb_4bit_use_double_quant=True)
    model = AutoModelForCausalLM.from_pretrained(a.base, quantization_config=quant, trust_remote_code=True,
                                                 torch_dtype=torch.bfloat16, device_map="auto")
    model.config.use_cache = False
    if quant is not None:
        model = prepare_model_for_kbit_training(model)
    lora = LoraConfig(r=a.lora_r, lora_alpha=a.lora_alpha, lora_dropout=a.lora_dropout, bias="none",
                      task_type="CAUSAL_LM",
                      target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"])
    model = get_peft_model(model, lora)
    model.print_trainable_parameters()

    tr = _load_examples(a.train)
    print(f"[train] {len(tr)} eğitim örneği (prompt-masked)", flush=True)
    train_ds = Dataset.from_list(tr).map(_tok_mask, remove_columns=["messages"])
    eval_ds = None
    if a.val and os.path.exists(a.val):
        ev = _load_examples(a.val)
        if ev:
            eval_ds = Dataset.from_list(ev).map(_tok_mask, remove_columns=["messages"])

    collator = DataCollatorForSeq2Seq(tok, label_pad_token_id=-100, padding="longest")
    args = TrainingArguments(
        output_dir=a.out, num_train_epochs=a.epochs,
        per_device_train_batch_size=a.batch, gradient_accumulation_steps=a.grad_accum,
        learning_rate=a.lr, lr_scheduler_type="cosine", warmup_ratio=0.03,
        logging_steps=5, save_strategy="epoch", bf16=True, seed=a.seed, report_to=[],
        eval_strategy=("epoch" if eval_ds is not None else "no"))
    trainer = Trainer(model=model, args=args, train_dataset=train_ds, eval_dataset=eval_ds,
                      data_collator=collator)
    trainer.train()
    trainer.save_model(a.out)
    tok.save_pretrained(a.out)
    print(f"[train] adapter kaydedildi → {a.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
