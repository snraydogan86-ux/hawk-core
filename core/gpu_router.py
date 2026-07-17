"""
HAWK Hibrit GPU Router — yerel-önce compute yönlendirme + yaşam döngüsü (Md.9/29).

Mevcut model/sağlayıcı seçiminin (model_router.py, provider_router.py) ÜSTÜNE
GPU KONUM + YAŞAM DÖNGÜSÜ katmanı ekler:
  - Yerel PC/GPU açıksa ve yeterliyse → YEREL (ücretsiz)
  - Yerel kapalı/yetersiz/yoğun → en ucuz uygun ONLINE GPU (bütçe varsa)
  - Online GPU iş bitince KAPANIR (idle-timeout) — sürekli açık kalmaz
  - Bütçe tavanı + otomatik kapanma
  - Video/görsel ağır işler AYRI kuyrukta

SAF karar mantığı (canlı GPU'ya bağlanmaz) → test edilebilir. Gerçek yerel-GPU
sağlık probu ve online sağlayıcı start/stop AYRI entegrasyon katmanıdır.
"""
from __future__ import annotations

import os
from typing import Any, Dict

# İş türü → gereken minimum VRAM (GB) tahmini
_MIN_VRAM = {"video": 24.0, "image": 12.0, "code": 24.0, "chat": 8.0}
# Online GPU idle kapanma eşiği (sn) — sürekli açık kalmasın
IDLE_SHUTDOWN_SEC = int(os.getenv("HAWK_GPU_IDLE_SHUTDOWN_SEC", "120") or 120)
# Aylık GPU harcama tavanı (USD)
GPU_MONTHLY_CAP_USD = float(os.getenv("HAWK_GPU_MONTHLY_CAP_USD", "300") or 300)

HEAVY = ("video", "image")


def _tier_for(kind: str, complexity: str) -> str:
    """İş → model katmanı (model_router TIER ile uyumlu)."""
    if kind in ("video", "image"):
        return "tier3"
    if kind == "code" or complexity in ("high", "reasoning"):
        return "tier3"
    if complexity == "medium":
        return "tier2"
    return "tier1"


def route_compute(kind: str, *, complexity: str = "low",
                  local_gpu: Dict[str, Any] | None = None,
                  online_budget_ok: bool = True,
                  prefer_local: bool = True) -> Dict[str, Any]:
    """
    Bir compute işini yönlendir.
    local_gpu: {"available": bool, "vram_gb": float, "load": 0..1}
    Döner: {target: local|online|api|queued, queue: text|heavy, tier, reason}
    """
    g = local_gpu or {"available": False, "vram_gb": 0.0, "load": 1.0}
    kind = (kind or "chat").lower()
    tier = _tier_for(kind, complexity)
    heavy = kind in HEAVY
    queue = "heavy" if heavy else "text"
    need_vram = _MIN_VRAM.get(kind, 8.0)

    local_fit = (
        prefer_local and g.get("available")
        and float(g.get("vram_gb", 0) or 0) >= need_vram
        and float(g.get("load", 1) or 1) < (0.8 if heavy else 0.9)
    )

    if local_fit:
        return {"target": "local", "queue": queue, "tier": tier,
                "reason": "yerel GPU yeterli — ücretsiz"}

    # Yerel yetersiz/kapalı
    if heavy:
        if online_budget_ok:
            return {"target": "online", "queue": queue, "tier": tier,
                    "reason": "ağır iş, yerel yetersiz → online GPU (iş bitince kapanır)"}
        return {"target": "queued", "queue": queue, "tier": tier,
                "reason": "bütçe yok → ağır iş kuyrukta bekler"}

    # Metin işleri: basit → ucuz yönetilen API; zor → online güçlü GPU
    if tier == "tier1":
        return {"target": "api", "queue": queue, "tier": tier,
                "reason": "basit iş, yerel yok → ucuz yönetilen API"}
    if online_budget_ok:
        return {"target": "online", "queue": queue, "tier": tier,
                "reason": "zor iş, yerel yok → online güçlü GPU"}
    return {"target": "api", "queue": queue, "tier": tier,
            "reason": "online bütçe yok → yönetilen API (yedek)"}


def should_shutdown_online(*, idle_seconds: float, active_jobs: int,
                           idle_threshold: int = IDLE_SHUTDOWN_SEC) -> Dict[str, Any]:
    """Online GPU kapatılmalı mı? Aktif iş yoksa ve idle eşiği aşıldıysa EVET."""
    if active_jobs > 0:
        return {"shutdown": False, "reason": "aktif iş var"}
    if idle_seconds >= idle_threshold:
        return {"shutdown": True, "reason": f"{int(idle_seconds)}s idle ≥ {idle_threshold}s — kapat"}
    return {"shutdown": False, "reason": f"idle {int(idle_seconds)}s < {idle_threshold}s"}


def can_spend_gpu(monthly_spent: float, est_cost: float,
                  cap: float = GPU_MONTHLY_CAP_USD) -> Dict[str, Any]:
    """Aylık GPU harcama tavanı kontrolü."""
    if monthly_spent + est_cost > cap:
        return {"allow": False, "reason": "monthly_gpu_cap_exceeded", "cap": cap,
                "remaining": round(max(0.0, cap - monthly_spent), 2)}
    return {"allow": True, "reason": "ok", "remaining": round(cap - monthly_spent - est_cost, 2)}
