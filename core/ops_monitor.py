"""
FAZ 3 — Ops izleme: worker heartbeat/registry + kaynak (CPU/RAM/disk/GPU) + günlük bütçe kapısı.

- Worker registry: in-process worker'lar heartbeat gönderir; freshness ile online/offline.
- Kaynak: os.getloadavg (CPU), /proc/meminfo (RAM), shutil (disk), gpu_state (GPU) — psutil YOK.
- Bütçe: hawk_cost_log günlük toplamı >= HAWK_DAILY_HARD_USD → budget_blocked() True → yeni iş başlamaz.
- monitor_loop: periyodik snapshot; eşik aşımında alarm; bütçe aşımında blok + alarm.
"""
from __future__ import annotations
import os
import shutil
import time

_WORKERS: dict = {}
_ONLINE_S = int(os.getenv("HAWK_WORKER_ONLINE_S", "90"))
_BUDGET_BLOCK = {"blocked": False, "spent": 0.0, "cap": 0.0, "ts": 0.0}
_DISK_WARN = float(os.getenv("HAWK_DISK_WARN_PCT", "90"))
_MEM_WARN = float(os.getenv("HAWK_MEM_WARN_PCT", "92"))
_alert_fn = None   # app.py enjekte eder (best-effort alarm)


def set_alert(fn):
    global _alert_fn
    _alert_fn = fn


def _alert(msg, level="high"):
    try:
        if _alert_fn:
            _alert_fn(msg, level=level, cooldown_key="ops_monitor")
    except Exception:
        pass


def heartbeat(worker_id: str, *, capabilities=None, meta=None):
    _WORKERS[worker_id] = {"last_hb": time.time(), "capabilities": list(capabilities or []),
                           "meta": meta or {}}


def workers() -> list:
    now = time.time()
    out = []
    for wid, w in _WORKERS.items():
        age = now - w["last_hb"]
        out.append({"worker_id": wid, "online": age < _ONLINE_S, "last_hb_age_s": round(age, 1),
                    "capabilities": w["capabilities"], "meta": w.get("meta", {})})
    return out


def _mem_pct() -> float:
    try:
        info = {}
        with open("/proc/meminfo") as f:
            for line in f:
                k, _, v = line.partition(":")
                info[k.strip()] = float(v.strip().split()[0])  # kB
        total = info.get("MemTotal", 0)
        avail = info.get("MemAvailable", info.get("MemFree", 0))
        return round(100.0 * (1 - avail / total), 1) if total else 0.0
    except Exception:
        return 0.0


def resource_snapshot() -> dict:
    snap = {"ts": time.time()}
    try:
        la = os.getloadavg()
        snap["cpu_load1"] = round(la[0], 2)
        snap["cpu_count"] = os.cpu_count() or 1
        snap["cpu_pct_est"] = round(min(100.0, 100.0 * la[0] / (os.cpu_count() or 1)), 1)
    except Exception:
        pass
    snap["mem_pct"] = _mem_pct()
    try:
        du = shutil.disk_usage("/")
        snap["disk_pct"] = round(100.0 * du.used / du.total, 1)
        snap["disk_free_gb"] = round(du.free / 1e9, 1)
    except Exception:
        pass
    try:
        from core import gpu_state as _gs
        snap["gpu"] = _gs.route_live() if hasattr(_gs, "route_live") else {}
    except Exception:
        snap["gpu"] = {}
    return snap


async def daily_spend_usd() -> float:
    """Bugünkü ARKA-PLAN öğrenme harcaması (hawk_cost_log, origin='background'). Tablo yoksa 0.
    KURAL: $3/$10 arka-plan bütçe kapısı YALNIZ arka-plan harcamasını sayar — kullanıcı sohbeti/görsel/
    ses/serving (origin='user') bu toplama GİRMEZ, dolayısıyla kullanıcı araçları bütçeyle kısıtlanamaz."""
    try:
        from core.pg_memory import _get_pool
        pool = await _get_pool()
        async with pool.acquire() as c:
            reg = await c.fetchval("SELECT to_regclass('public.hawk_cost_log')")
            if not reg:
                return 0.0
            has_origin = await c.fetchval(
                "SELECT 1 FROM information_schema.columns WHERE table_name='hawk_cost_log' AND column_name='origin'")
            if has_origin:
                v = await c.fetchval(
                    "SELECT COALESCE(SUM(est_cost_usd),0) FROM hawk_cost_log "
                    "WHERE created_at >= date_trunc('day', now()) AND origin='background'")
            else:  # migration öncesi geriye-uyum
                v = await c.fetchval(
                    "SELECT COALESCE(SUM(est_cost_usd),0) FROM hawk_cost_log WHERE created_at >= date_trunc('day', now())")
            return float(v or 0.0)
    except Exception:
        return 0.0


def budget_blocked() -> bool:
    return bool(_BUDGET_BLOCK.get("blocked"))


def budget_state() -> dict:
    return dict(_BUDGET_BLOCK)


async def check_budget() -> dict:
    from core import cost_guard as _cg
    cap = float(_cg.limits().get("daily_hard_usd", 10.0))
    spent = await daily_spend_usd()
    was = _BUDGET_BLOCK.get("blocked")
    blocked = spent >= cap
    _BUDGET_BLOCK.update({"blocked": blocked, "spent": round(spent, 4), "cap": cap, "ts": time.time()})
    if blocked and not was:
        _alert(f"Günlük bütçe aşıldı: ${spent:.2f} ≥ ${cap:.2f} — yeni işler durduruldu.", level="critical")
    return dict(_BUDGET_BLOCK)


async def monitor_loop():
    import asyncio
    interval = int(os.getenv("HAWK_OPS_MONITOR_S", "60"))
    print(f"[HAWK][ops] monitor başladı ({interval}s)", flush=True)
    while True:
        try:
            await asyncio.sleep(interval)
            snap = resource_snapshot()
            await check_budget()
            if snap.get("disk_pct", 0) >= _DISK_WARN:
                _alert(f"Disk doluluk %{snap['disk_pct']} (boş {snap.get('disk_free_gb')}GB)", level="high")
            if snap.get("mem_pct", 0) >= _MEM_WARN:
                _alert(f"RAM doluluk %{snap['mem_pct']}", level="high")
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
