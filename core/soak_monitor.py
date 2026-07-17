"""
FAZ 14-15 — Soak (dayanıklılık) izleme. 24-saat + 7-gün KESİNTİSİZ çalışma kanıtı.

DÜRÜST KURAL: 24 saat GEÇMEDEN '24s tamam', 7 gün GEÇMEDEN '7/24 kanıtlandı' DENMEZ. Bu modül
soak SAATİNİ başlatır + gerekli olayları kaydeder; tamamlanma yalnız gerçek geçen-zamanla olur.

Gerekli olaylar (24s içinde en az 1'er): research_cycle, sandbox_experiment, improvement_proposal,
pc_task, hawkbase_coldstart, idle_shutdown, rollback, backup, worker_reconnect.
"""
from __future__ import annotations
import json
import os
import time

STATE = os.path.join(os.getenv("HAWK_MEMORY_DIR", "/data/hawk_memory"), "soak_state.json")
REQUIRED = ["research_cycle", "sandbox_experiment", "improvement_proposal", "pc_task",
            "hawkbase_coldstart", "idle_shutdown", "rollback", "backup", "worker_reconnect"]


def _load() -> dict:
    try:
        with open(STATE) as f:
            return json.load(f)
    except Exception:
        return {}


def _save(d: dict):
    tmp = STATE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f)
    os.replace(tmp, STATE)


def start(now: float) -> dict:
    """Soak saatini başlat (idempotent — zaten başladıysa dokunmaz)."""
    d = _load()
    if not d.get("started_at"):
        d = {"started_at": now, "events": {}, "incidents": []}
        _save(d)
    return d


def record_event(etype: str, now: float, detail: str = ""):
    """Bir soak olayını kaydet (ilk+son görülme + sayaç)."""
    d = _load()
    if not d.get("started_at"):
        return
    ev = d.setdefault("events", {}).setdefault(etype, {"count": 0, "first": now, "last": now})
    ev["count"] += 1
    ev["last"] = now
    if detail:
        ev["detail"] = detail[:120]
    _save(d)


def record_incident(kind: str, now: float, detail: str = ""):
    """Kritik olay/hata (P0/P1) kaydı — soak temizliği için."""
    d = _load()
    d.setdefault("incidents", []).append({"kind": kind, "ts": now, "detail": detail[:160]})
    _save(d)


def status(now: float) -> dict:
    d = _load()
    if not d.get("started_at"):
        return {"started": False, "note": "soak başlatılmadı"}
    elapsed_h = (now - d["started_at"]) / 3600.0
    events = d.get("events", {})
    seen = [e for e in REQUIRED if e in events]
    missing = [e for e in REQUIRED if e not in events]
    return {
        "started": True,
        "elapsed_hours": round(elapsed_h, 2),
        "elapsed_days": round(elapsed_h / 24.0, 2),
        "events_seen": seen,
        "events_missing": missing,
        "incidents": d.get("incidents", []),
        # DÜRÜST tamamlanma — yalnız gerçek geçen-zaman + olaylar + 0-incident
        "soak_24h_complete": elapsed_h >= 24.0 and not missing and len(d.get("incidents", [])) == 0,
        "soak_7d_complete": elapsed_h >= 168.0 and not missing and len(d.get("incidents", [])) == 0,
        "note": ("24 saat GEÇMEDEN '24s tamam' denmez; 7 gün GEÇMEDEN '7/24 kanıtlandı' denmez"),
    }
