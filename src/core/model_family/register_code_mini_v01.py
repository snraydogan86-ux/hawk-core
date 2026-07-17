#!/usr/bin/env python3
"""
hawk-code-v0.1 ve hawk-mini-v0.1'i registry'ye kaydeder (GPU'suz, eğitim yok, DRAFT).

hawk-code: base = open foundation model (PoC'te kod 9/10; tool-calling ZAYIF 1/5 —
           fine-tune'da giderilecek bilinen sınır).
hawk-mini: base = open foundation model (router/intent/dil-tespit/hızlı tool-routing).
           DÜRÜST NOT: HENÜZ BENCHMARK EDİLMEDİ — Phase A tasarım seçimi, eval gerekli.

Her ikisi de ModelRegistry.register ile doğrulanır → manifest yazılır. status=DRAFT,
production_version=None. benchmark_scores base_model referansıdır (modelin kendi skoru değil).
"""
from __future__ import annotations

import json
import os
import time

from core.model_family import ModelRegistry, ModelVersion, ModelStatus
from core.model_family.plans import CODE_PLAN

HERE = os.path.dirname(__file__)
REG = os.path.join(HERE, "registry")


def _code() -> ModelVersion:
    return ModelVersion(
        model_id="hawk-code-v0.1", family="code", role="hawk_code", version="v0.1",
        base_model="OpenFoundation/Model", adapter_type="lora",
        tokenizer="OpenFoundation/Model", quantization="awq",
        context_length=32768, supported_languages=("code", "tr", "en"),
        capabilities=tuple(CODE_PLAN["hawk-code-v0.1"]["goals"]),
        benchmark_scores={
            "_note": "base_model (open foundation model) PoC referansı — hawk-code-v0.1 kendi skoru değil. "
                     "60-test ESKİ harness (dil-fix öncesi) → en_natural düşük artefakt.",
            "code_10": "9/10", "structured_json_5": "5/5",
            "tool_calling_5": "1/5 (ZAYIF — fine-tune'da giderilecek)",
            "results_hash": "b5f8e0202361de99f657769a55a2cf43",
        },
        safety_scores={"hawk_mem_sec_5": "3/5"},
        latency={"tokens_per_s": 46.9, "avg_ms": 1898},
        throughput=46.9, vram_gb=6.0, license="apache-2.0",
        provenance="PoC 2026-07-12: open foundation model kod uzmanı (kod 9/10). tool-calling zayıf → "
                   "fine-tune + reviewer uyumu ile giderilecek. HAWK Code rolü #1.",
        created_at=time.time(), status=ModelStatus.DRAFT,
    )


def _mini() -> ModelVersion:
    return ModelVersion(
        model_id="hawk-mini-v0.1", family="mini", role="hawk_mini", version="v0.1",
        base_model="OpenFoundation/Model", adapter_type="lora",
        tokenizer="OpenFoundation/Model", quantization="none",
        context_length=32768, supported_languages=("tr", "en", "multilingual"),
        capabilities=("router", "intent_classification", "language_detection",
                      "fast_tool_routing", "cheap_always_on"),
        benchmark_scores={
            "_note": "HENÜZ BENCHMARK EDİLMEDİ — Phase A tasarım seçimi (router/sınıflandırma). "
                     "hawk-base'e delege eden hızlı, ucuz, her-zaman-açık katman. Eval GEREKLİ.",
        },
        safety_scores={"_note": "ölçülmedi"},
        latency={"_note": "ölçülmedi; ~1-2GB VRAM, CPU'da bile çalışabilir (tahmin)"},
        vram_gb=2.0, license="apache-2.0",
        provenance="Phase A tasarım: router/intent/dil-tespit rolü. open foundation model "
                   "(Apache-2.0). BENCHMARK EDİLMEDİ — sonraki adım eval + fine-tune.",
        created_at=time.time(), status=ModelStatus.DRAFT,
    )


def main() -> None:
    reg = ModelRegistry()
    os.makedirs(REG, exist_ok=True)
    for mv in (_code(), _mini()):
        reg.register(mv)                                   # şema doğrulaması
        out = os.path.join(REG, f"{mv.model_id}.json")
        json.dump(mv.to_dict(), open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"KAYDEDİLDİ: {mv.model_id} (role={mv.role}, base={mv.base_model}, "
              f"status={mv.status.value})")
    print(f"production_version(hawk_code) = {reg.production_version('hawk_code')} (None olmalı)")
    print(f"production_version(hawk_mini) = {reg.production_version('hawk_mini')} (None olmalı)")
    print("NOT: hawk-mini HENÜZ BENCHMARK EDİLMEDİ (tasarım seçimi); hawk-code tool-calling zayıf.")


if __name__ == "__main__":
    main()
