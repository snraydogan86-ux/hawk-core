"""
FAZ 21 — Ölçek / dayanıklılık (scale / resilience).

Yük altında GRACEFUL DEGRADATION: kaynak baskısında (disk/mem/load/bütçe) düşük-öncelik işler
ertelenir/reddedilir, kritik işler korunur. Circuit breaker: sürekli-hata veren bağımlılık
geçici devre-dışı. Backpressure: kuyruk dolunca yeni iş reddedilir (crash yerine).
"""
from __future__ import annotations
import time

# ---- Circuit Breaker (bağımlılık başına) ----
_CB: dict[str, dict] = {}
_CB_FAIL_THRESHOLD = 5        # ardışık hata → aç
_CB_COOLDOWN_S = 30.0         # açık kalma süresi → sonra half-open


def cb_record(key: str, success: bool) -> None:
    st = _CB.setdefault(key, {"fails": 0, "state": "closed", "opened_at": 0.0})
    if success:
        st["fails"] = 0; st["state"] = "closed"
    else:
        st["fails"] += 1
        if st["fails"] >= _CB_FAIL_THRESHOLD:
            st["state"] = "open"; st["opened_at"] = time.time()


def cb_allow(key: str) -> bool:
    """Bu bağımlılığa istek gönderilebilir mi? open → cooldown sonrası half-open (tek deneme)."""
    st = _CB.get(key)
    if not st or st["state"] == "closed":
        return True
    if st["state"] == "open":
        if time.time() - st["opened_at"] >= _CB_COOLDOWN_S:
            st["state"] = "half_open"; return True   # tek deneme izni
        return False
    return True  # half_open → dene (başarı closed'a, hata tekrar open'a çeker)


def cb_state() -> dict:
    return {k: {"state": v["state"], "fails": v["fails"]} for k, v in _CB.items()}


# ---- Graceful degradation seviyesi ----
def degradation_level(snapshot: dict | None = None) -> dict:
    """0=normal, 1=elevated (düşük-öncelik ertele), 2=critical (yalnız kritik). Kaynak baskısından."""
    snap = snapshot
    if snap is None:
        try:
            from core import ops_monitor as _ops
            snap = _ops.resource_snapshot()
        except Exception:
            snap = {}
    disk = float(snap.get("disk_pct") or 0)
    mem = float(snap.get("mem_pct") or 0)
    load = float(snap.get("cpu_pct_est") or 0)
    reasons = []
    level = 0
    if disk >= 97 or mem >= 95:
        level = 2; reasons.append(f"kritik kaynak: disk%{disk} mem%{mem}")
    elif disk >= 90 or mem >= 85 or load >= 90:
        level = max(level, 1); reasons.append(f"yüksek yük: disk%{disk} mem%{mem} load%{load}")
    try:
        from core import ops_monitor as _ops
        if _ops.budget_blocked():
            level = max(level, 1); reasons.append("günlük bütçe aşıldı")
    except Exception:
        pass
    return {"level": level, "label": ("normal", "elevated", "critical")[level], "reasons": reasons}


# ---- Admission control (backpressure) ----
_PRIORITY = {"critical": 3, "high": 2, "normal": 1, "low": 0}


def admit(priority: str = "normal", *, queue_depth: int = 0, queue_cap: int = 500,
          snapshot: dict | None = None) -> dict:
    """Yeni iş kabul/ertele/reddet. Kaynak baskısı + kuyruk derinliğine göre backpressure."""
    p = _PRIORITY.get(priority, 1)
    # backpressure: kuyruk dolu → yalnız yüksek-öncelik
    if queue_depth >= queue_cap:
        if p >= _PRIORITY["high"]:
            return {"admit": True, "mode": "admitted", "note": "kuyruk dolu ama yüksek-öncelik"}
        return {"admit": False, "mode": "rejected", "reason": f"kuyruk dolu ({queue_depth}/{queue_cap})"}
    deg = degradation_level(snapshot)
    if deg["level"] >= 2 and p < _PRIORITY["critical"]:
        return {"admit": False, "mode": "rejected", "reason": "kritik kaynak baskısı — yalnız kritik iş",
                "degradation": deg}
    if deg["level"] >= 1 and p <= _PRIORITY["low"]:
        return {"admit": False, "mode": "deferred", "reason": "yüksek yük — düşük-öncelik ertelendi",
                "degradation": deg}
    return {"admit": True, "mode": "admitted", "degradation": deg}


def readiness() -> dict:
    """Gerçek sağlık: ready|degraded|not_ready (kaynak + circuit breaker durumu)."""
    deg = degradation_level()
    open_cbs = [k for k, v in _CB.items() if v["state"] == "open"]
    if deg["level"] >= 2:
        status = "not_ready"
    elif deg["level"] == 1 or open_cbs:
        status = "degraded"
    else:
        status = "ready"
    return {"status": status, "degradation": deg, "open_circuits": open_cbs}
