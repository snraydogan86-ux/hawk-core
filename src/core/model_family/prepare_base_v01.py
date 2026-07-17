#!/usr/bin/env python3
"""
hawk-base-v0.1'i eğitime HAZIRLAR (GPU'suz, EĞİTİM BAŞLATMAZ).

Yapar:
  1) draft → dataset_preparing → approved_for_training (dataset'i bağlar)
  2) TrainingJob TASLAĞI oluşturur (LoRA config + hard_cost_limit) → status=PENDING_APPROVAL
Yapmaz:
  - training başlatma (approved_for_training → training = ADMIN ONAYI)
  - GPU açma / ücretli servis (AYRI onay)

NOT: hawk-dataset-v0.1 şu an 10 KÜRASYONLU seed örneğidir — gerçek fine-tune için KÜÇÜK.
Bu adım altyapı/taslaktır; gerçek eğitim öncesi dataset genişletilmeli. Maliyet tahminleri
VARSAYIMDIR (open foundation model QLoRA, bulut GPU ~$/saat).
"""
from __future__ import annotations

import json
import os
import time

from core.model_family import (
    ModelRegistry, ModelStatus, TrainingRegistry, TrainingJob, JobStatus,
)
from core.model_family.model_registry import ModelVersion

HERE = os.path.dirname(__file__)
REG = os.path.join(HERE, "registry")
MODEL_MF = os.path.join(REG, "hawk-base-v0.1.json")
DATASET_MF = os.path.join(REG, "hawk-dataset-v0.1.json")
JOB_MF = os.path.join(REG, "training-job-hawk-base-v0.1.json")

DATASET_VERSION = "hawk-dataset-v0.1"


def main() -> None:
    # -- model registry: manifest'ten yükle --
    mv = ModelVersion.from_dict(json.load(open(MODEL_MF, encoding="utf-8")))
    reg = ModelRegistry(); reg.register(mv)

    if not os.path.exists(DATASET_MF):
        raise SystemExit("hawk-dataset-v0.1 manifest yok — önce build_dataset_v01 çalıştır.")

    # -- ilerlet: draft → dataset_preparing → approved_for_training --
    if mv.status == ModelStatus.DRAFT:
        reg.advance(mv.model_id, ModelStatus.DATASET_PREPARING, reason="hawk-dataset-v0.1 bağlandı")
        mv.training_dataset_version = DATASET_VERSION
    if mv.status == ModelStatus.DATASET_PREPARING:
        reg.advance(mv.model_id, ModelStatus.APPROVED_FOR_TRAINING,
                    reason="dataset frozen + admin hazırlık onayı")
    json.dump(mv.to_dict(), open(MODEL_MF, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    # -- TrainingJob TASLAĞI (PENDING_APPROVAL; başlatılmaz) --
    treg = TrainingRegistry()
    job = TrainingJob(
        training_job_id="tj-hawk-base-v0.1",
        model_target="hawk-base-v0.1",
        base_model="OpenFoundation/Model",
        dataset_version=DATASET_VERSION,
        method="qlora",
        lora_config={"r": 16, "alpha": 32, "dropout": 0.05,
                     "target_modules": ["q_proj", "k_proj", "v_proj", "o_proj",
                                        "gate_proj", "up_proj", "down_proj"]},
        learning_rate=1e-4, sequence_length=4096, batch_size=4,
        gradient_accumulation=8, epochs=3, seed=42,
        hardware="1x L40S 48GB (veya A100-40GB)  [VARSAYIM]",
        estimated_cost=8.0,          # VARSAYIM: ~3-5 GPU-saat × ~$0.8-1.9/sa + overhead
        hard_cost_limit=20.0,        # sert tavan; aşılırsa başlamaz
        max_runtime_s=4 * 3600,
        checkpoint_interval=200,
        output_location="artifacts/hawk-base-v0.1/",
    )
    treg.create(job)                 # → PENDING_APPROVAL (otomatik başlamaz)
    json.dump(job.to_dict(), open(JOB_MF, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    print("=== hawk-base-v0.1 EĞİTİME HAZIRLANDI (eğitim BAŞLATILMADI) ===")
    print(f" model durumu: {mv.status.value}  (approved_for_training olmalı)")
    print(f" bağlı dataset: {mv.training_dataset_version}")
    print(f" training job: {job.training_job_id} → durum {job.status.value}")
    print(f"   method={job.method} lora_r={job.lora_config['r']} epochs={job.epochs} "
          f"est=${job.estimated_cost} hard_limit=${job.hard_cost_limit}")
    print(" SONRAKİ (AYRI AÇIK ONAY): approve → approve_gpu → start (GPU + eğitim)")
    print(f" manifestler: {os.path.basename(MODEL_MF)}, {os.path.basename(JOB_MF)}")


if __name__ == "__main__":
    main()
