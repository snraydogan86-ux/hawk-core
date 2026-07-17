#!/usr/bin/env python3
"""
hawk-reasoning-v0.1 ve hawk-vision-v0.1'i registry'ye kaydeder (GPU'suz, eğitim yok, DRAFT).

hawk-reasoning: base = open foundation model (Apache-2.0, live config ile teyitli). PoC'te reasoning
  10/10 + en iyi Türkçe (tr 10/15) AMA yavaş (25.8 tok/s, thinking-OFF) → Base'e uygun DEĞİL,
  REASONING rolüne uygun (thinking-ON derin reasoning için). Daha büyük 32B ileride opsiyon.
hawk-vision: base = open foundation model (Apache-2.0 — resmî model-hub frontmatter ile DOĞRULANDI,
  image-text-to-text). DÜRÜST NOT: görsel harness'ı YOK → HENÜZ BENCHMARK EDİLMEDİ.

Her ikisi de ModelRegistry.register ile doğrulanır. status=DRAFT, production_version=None.
"""
from __future__ import annotations

import json
import os
import time

from core.model_family import ModelRegistry, ModelVersion, ModelStatus

HERE = os.path.dirname(__file__)
REG = os.path.join(HERE, "registry")


def _reasoning() -> ModelVersion:
    return ModelVersion(
        model_id="hawk-reasoning-v0.1", family="reasoning", role="hawk_reasoning",
        version="v0.1", base_model="OpenFoundation/Model", adapter_type="lora",
        tokenizer="OpenFoundation/Model", quantization="awq",
        context_length=32768, supported_languages=("tr", "en", "multilingual"),
        capabilities=("derin_reasoning", "zor_analiz", "uzun_gorev", "multi_agent_plan",
                      "thinking_mode"),
        benchmark_scores={
            "_note": "base_model (open foundation model) PoC referansı (thinking-OFF hızlı-chat modunda). "
                     "Reasoning rolünde thinking-ON kullanılır → daha iyi reasoning, daha yavaş.",
            "reasoning_10": "10/10", "tr_natural_15": "10/15 (en iyi Türkçe)",
            "tool_calling_5": "3/5 (zayıf)", "tokens_per_s": 25.75,
            "results_hash": "b524047a8616cfcb313c0666ba24757c",
        },
        safety_scores={"hawk_mem_sec_5": "4/5"},
        latency={"tokens_per_s": 25.75, "avg_ms": 4415, "thinking": "off@PoC; reasoning'de on"},
        throughput=25.75, vram_gb=12.0, license="apache-2.0",
        provenance="PoC 2026-07-12: open foundation model reasoning 10/10 + en iyi TR ama yavaş → Base'e "
                   "uygun değil, REASONING rolü adayı. 32B ileride opsiyon (daha büyük GPU).",
        created_at=time.time(), status=ModelStatus.DRAFT,
    )


def _vision() -> ModelVersion:
    return ModelVersion(
        model_id="hawk-vision-v0.1", family="vision", role="hawk_vision",
        version="v0.1", base_model="OpenFoundation/Model", adapter_type="lora",
        tokenizer="OpenFoundation/Model", quantization="none",
        context_length=32768, supported_languages=("tr", "en", "multilingual"),
        capabilities=("gorsel_analiz", "image_text_to_text", "belge_okuma", "grafik_yorumlama"),
        benchmark_scores={
            "_note": "HENÜZ BENCHMARK EDİLMEDİ — HAWK benchmark'ında görsel test yok. "
                     "Vision eval ayrı harness gerektirir (görsel giriş + rubric).",
        },
        safety_scores={"_note": "ölçülmedi"},
        latency={"_note": "ölçülmedi"},
        vram_gb=10.0, license="apache-2.0",   # resmî model-hub frontmatter ile DOĞRULANDI
        provenance="Phase A tasarım: HAWK Vision rolü. open foundation model (Apache-2.0, "
                   "image-text-to-text — lisans resmî model-hub frontmatter ile teyitli). BENCHMARK "
                   "EDİLMEDİ — sonraki adım vision eval harness + fine-tune.",
        created_at=time.time(), status=ModelStatus.DRAFT,
    )


def main() -> None:
    reg = ModelRegistry()
    os.makedirs(REG, exist_ok=True)
    for mv in (_reasoning(), _vision()):
        reg.register(mv)
        out = os.path.join(REG, f"{mv.model_id}.json")
        json.dump(mv.to_dict(), open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"KAYDEDİLDİ: {mv.model_id} (role={mv.role}, base={mv.base_model}, "
              f"status={mv.status.value}, license={mv.license})")
    print(f"production_version(hawk_reasoning) = {reg.production_version('hawk_reasoning')} (None)")
    print(f"production_version(hawk_vision) = {reg.production_version('hawk_vision')} (None)")
    print("NOT: hawk-vision BENCHMARK EDİLMEDİ (görsel harness yok); hawk-reasoning thinking-ON kullanır.")


if __name__ == "__main__":
    main()
