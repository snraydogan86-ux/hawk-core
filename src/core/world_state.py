"""
HAWK World State — Global platform durum yöneticisi.

Platform genelinde paylaşılan durum bilgisini tek noktadan yönetir:
  - Aktif oturumlar
  - Sistem modu (production / maintenance / degraded)
  - Hata istatistikleri
  - Önemli platform olayları
  - Feature flag sistemi
  - Anlık metrikler

Tüm bileşenler bu modülü import ederek sistem durumunu okuyabilir/yazabilir.
"""
from __future__ import annotations

import time
from collections import deque
from typing import Any, Dict, List, Optional

# ─────────────────────────── CONSTANTS ─────────────────────────────

MODE_PRODUCTION   = "production"
MODE_MAINTENANCE  = "maintenance"
MODE_DEGRADED     = "degraded"
MODE_RECOVERY     = "recovery"


# ─────────────────────────── WORLD STATE ───────────────────────────

class WorldState:
    """Platform genelinde paylaşılan tek durum nesnesi (singleton pattern)."""

    _mode: str = MODE_PRODUCTION
    _started_at: float = time.time()

    # Sayaçlar
    _total_requests: int = 0
    _total_errors: int = 0
    _active_sessions: int = 0

    # Özellik bayrakları
    _flags: Dict[str, Any] = {}

    # Olay geçmişi (son 200 olay)
    _events: deque = deque(maxlen=200)

    # Servis durumları
    _services: Dict[str, Dict] = {}

    # ── Mode ────────────────────────────────────────────────────────

    @classmethod
    def set_mode(cls, mode: str):
        old = cls._mode
        cls._mode = mode
        cls._add_event("mode_change", f"{old} → {mode}", source="world_state")

    @classmethod
    def get_mode(cls) -> str:
        return cls._mode

    @classmethod
    def is_production(cls) -> bool:
        return cls._mode == MODE_PRODUCTION

    @classmethod
    def is_degraded(cls) -> bool:
        return cls._mode in (MODE_DEGRADED, MODE_MAINTENANCE, MODE_RECOVERY)

    # ── Counters ─────────────────────────────────────────────────────

    @classmethod
    def inc_requests(cls, count: int = 1):
        cls._total_requests += count

    @classmethod
    def inc_errors(cls, count: int = 1):
        cls._total_errors += count
        if cls._total_errors > 0 and cls._mode == MODE_PRODUCTION:
            error_rate = cls._total_errors / max(cls._total_requests, 1)
            if error_rate > 0.1:
                cls._mode = MODE_DEGRADED

    @classmethod
    def set_active_sessions(cls, count: int):
        cls._active_sessions = count

    @classmethod
    def reset_errors(cls):
        cls._total_errors = 0
        if cls._mode == MODE_DEGRADED:
            cls._mode = MODE_PRODUCTION
            cls._add_event("mode_change", "degraded → production (error reset)", source="self_healer")

    # ── Feature Flags ────────────────────────────────────────────────

    @classmethod
    def set_flag(cls, key: str, value: Any):
        cls._flags[key] = value

    @classmethod
    def get_flag(cls, key: str, default: Any = None) -> Any:
        return cls._flags.get(key, default)

    @classmethod
    def remove_flag(cls, key: str):
        cls._flags.pop(key, None)

    # ── Services ─────────────────────────────────────────────────────

    @classmethod
    def update_service(cls, name: str, status: str, details: Optional[Dict] = None):
        cls._services[name] = {
            "status": status,
            "updated_at": time.time(),
            **(details or {}),
        }

    @classmethod
    def get_service_status(cls, name: str) -> Optional[Dict]:
        return cls._services.get(name)

    @classmethod
    def all_services_healthy(cls) -> bool:
        return all(s.get("status") == "ok" for s in cls._services.values())

    # ── Events ──────────────────────────────────────────────────────

    @classmethod
    def _add_event(cls, event_type: str, detail: str, source: str = "system"):
        cls._events.append({
            "type": event_type,
            "detail": detail[:200],
            "source": source,
            "ts": time.time(),
        })

    @classmethod
    def emit(cls, event_type: str, detail: str, source: str = "system"):
        cls._add_event(event_type, detail, source)

    @classmethod
    def recent_events(cls, limit: int = 20, event_type: Optional[str] = None) -> List[Dict]:
        events = list(cls._events)
        if event_type:
            events = [e for e in events if e["type"] == event_type]
        return events[-limit:]

    # ── Snapshot ─────────────────────────────────────────────────────

    @classmethod
    def snapshot(cls) -> Dict[str, Any]:
        uptime = round(time.time() - cls._started_at)
        error_rate = cls._total_errors / max(cls._total_requests, 1)
        return {
            "mode": cls._mode,
            "uptime_seconds": uptime,
            "total_requests": cls._total_requests,
            "total_errors": cls._total_errors,
            "error_rate": round(error_rate, 4),
            "active_sessions": cls._active_sessions,
            "services": dict(cls._services),
            "flags": dict(cls._flags),
            "all_healthy": cls.all_services_healthy(),
            "recent_events": cls.recent_events(limit=10),
        }

    @classmethod
    def metrics(cls) -> Dict[str, Any]:
        """Prometheus/Grafana uyumlu metrik snapshot."""
        return {
            "hawk_requests_total": cls._total_requests,
            "hawk_errors_total": cls._total_errors,
            "hawk_active_sessions": cls._active_sessions,
            "hawk_uptime_seconds": round(time.time() - cls._started_at),
            "hawk_mode": cls._mode,
        }

    @classmethod
    def reset_counters(cls):
        cls._total_requests = 0
        cls._total_errors = 0


# ── Singleton export ─────────────────────────────────────────────────
world_state = WorldState()
