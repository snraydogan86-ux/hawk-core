"""
HAWK Online GPU sağlayıcı adaptörü (GPU cloud / Vast).

GÜVENLİK:
  - status(): SALT-OKUNUR (GPU cloud health) — %100 güvenli, gerçek çağrı.
  - start/stop: GATE'li — yalnız HAWK_GPU_LIVE=true VE ayrı bir pod id (HAWK_GPU_ONLINE_POD_ID)
    varsa gerçek çağrı yapar. Aksi halde DRY-RUN (hiçbir şey başlatmaz/durdurmaz).
  - HAWK BEYNİ (GPU_CLOUD_ENDPOINT_ID, serverless) start/stop'a ASLA hedef DEĞİL — sadece status okunur.

Böylece yanlışlıkla beynin kapanması imkânsız; gerçek start/stop ayrı bir iş-pod'una uygulanır.
"""
from __future__ import annotations

import os
from typing import Any, Dict

GPU_LIVE = os.getenv("HAWK_GPU_LIVE", "false").lower() in ("1", "true", "yes")
# Online fallback için AYRI iş-pod'u (beyin DEĞİL). Yoksa start/stop no-op.
ONLINE_POD_ID = os.getenv("HAWK_GPU_ONLINE_POD_ID", "")
ONLINE_USD_PER_HOUR = float(os.getenv("HAWK_GPU_ONLINE_USD_PER_HOUR", "0.5") or 0.5)


def _gpu_cloud_key() -> str:
    return os.getenv("GPU_CLOUD_API_KEY", "")


def available_providers() -> list:
    p = []
    if _gpu_cloud_key():
        p.append("gpu_cloud")
    if os.getenv("VAST_API_KEY"):
        p.append("vast")
    return p


def provider_status() -> Dict[str, Any]:
    return {
        "live": GPU_LIVE,
        "available": available_providers(),
        "online_pod_configured": bool(ONLINE_POD_ID),
        "usd_per_hour": ONLINE_USD_PER_HOUR,
        "note": "start/stop yalnız HAWK_GPU_LIVE=true + HAWK_GPU_ONLINE_POD_ID ile gerçek; "
                "beyin (GPU_CLOUD_ENDPOINT_ID) start/stop hedefi değildir.",
    }


async def gpu_cloud_health(endpoint_id: str = "") -> Dict[str, Any]:
    """SALT-OKUNUR GPU cloud serverless health (worker/iş durumu). Güvenli."""
    key = _gpu_cloud_key()
    eid = endpoint_id or os.getenv("GPU_CLOUD_ENDPOINT_ID", "")
    if not (key and eid):
        return {"ok": False, "error": "gpu_cloud_not_configured"}
    try:
        import httpx
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(f"https://api.gpu_cloud.ai/v2/{eid}/health",
                                 headers={"Authorization": f"Bearer {key}"})
        if r.status_code == 200:
            d = r.json()
            w = d.get("workers", {})
            return {"ok": True, "workers": w, "jobs": d.get("jobs", {}),
                    "active": (w.get("ready", 0) + w.get("running", 0)) > 0}
        return {"ok": False, "error": f"http_{r.status_code}", "detail": r.text[:160]}
    except Exception as e:
        return {"ok": False, "error": f"exc:{str(e)[:120]}"}


async def _gpu_cloud_pod_action(action: str) -> Dict[str, Any]:
    """GATE'li pod start/stop (GraphQL). Beyne DOKUNMAZ (ayrı pod)."""
    if not GPU_LIVE:
        return {"ok": True, "dry_run": True, "reason": "HAWK_GPU_LIVE!=true", "action": action}
    if not ONLINE_POD_ID:
        return {"ok": True, "dry_run": True, "reason": "HAWK_GPU_ONLINE_POD_ID yok", "action": action}
    key = _gpu_cloud_key()
    if not key:
        return {"ok": False, "error": "gpu_cloud_not_configured"}
    mutation = ("podResume(input:{podId:\"%s\", gpuCount:1})" % ONLINE_POD_ID if action == "start"
                else "podStop(input:{podId:\"%s\"})" % ONLINE_POD_ID)
    try:
        import httpx
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(f"https://api.gpu-cloud/graphql?api_key={key}",
                                  json={"query": "mutation { %s { id desiredStatus } }" % mutation})
        if r.status_code == 200 and "errors" not in r.json():
            return {"ok": True, "action": action, "pod": ONLINE_POD_ID, "result": r.json().get("data")}
        return {"ok": False, "error": "graphql_error", "detail": str(r.json())[:200]}
    except Exception as e:
        return {"ok": False, "error": f"exc:{str(e)[:120]}"}


async def start_online() -> Dict[str, Any]:
    return await _gpu_cloud_pod_action("start")


async def stop_online() -> Dict[str, Any]:
    return await _gpu_cloud_pod_action("stop")
