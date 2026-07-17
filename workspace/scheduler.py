"""
HAWK CORE — Scheduler / Background autonomous loop.

Sen yokken çalışır: belirli aralıkla bekleyen görevleri (Task Queue) uzman ajanlarla
işler (think_once). Güvenlik için VARSAYILAN KAPALI — açıkça başlatılır.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Dict, Optional

from . import core as CORE
from . import memory as M

_log = logging.getLogger("hawk.core.scheduler")

_running: bool = False
_task: Optional[asyncio.Task] = None
_interval: int = int(os.getenv("HAWK_CORE_TICK", "60"))  # saniye
_ticks: int = 0
_last: Dict[str, Any] = {}
_MAX_PER_TICK = 3  # bir turda en fazla görev (taşkınlık koruması)


async def _loop():
    global _ticks, _last
    _log.info("HAWK CORE scheduler başladı (interval=%ss)", _interval)
    while _running:
        try:
            done = 0
            for _ in range(_MAX_PER_TICK):
                res = await CORE.think_once()
                if res.get("idle"):
                    break
                done += 1
                _last = res
            _ticks += 1
            if done:
                _log.info("scheduler tick: %s görev işlendi", done)
            else:
                # Kuyruk boş → HAWK kendi görevini üretsin (self-direction, rate-limited)
                try:
                    from . import selfdirect
                    g = await selfdirect.maybe_generate()
                    if g.get("added"):
                        _log.info("self-direct: %s yeni görev üretildi", len(g["added"]))
                except Exception as e:  # noqa: BLE001
                    _log.warning("self-direct hatası: %s", e)
        except Exception as e:  # noqa: BLE001
            _log.warning("scheduler tick hatası: %s", e)
            try:
                await M.remember_episode("event", f"[scheduler] hata: {e}")
            except Exception:
                pass
        await asyncio.sleep(_interval)
    _log.info("HAWK CORE scheduler durdu")


def start(interval: Optional[int] = None) -> Dict[str, Any]:
    global _running, _task, _interval
    if interval:
        _interval = max(15, int(interval))
    if _running and _task and not _task.done():
        return {"ok": True, "already": True, "interval": _interval}
    _running = True
    _task = asyncio.create_task(_loop())
    return {"ok": True, "started": True, "interval": _interval}


def stop() -> Dict[str, Any]:
    global _running
    _running = False
    return {"ok": True, "stopped": True}


def status() -> Dict[str, Any]:
    return {
        "ok": True,
        "running": _running and bool(_task) and not _task.done(),
        "interval_s": _interval,
        "ticks": _ticks,
        "max_per_tick": _MAX_PER_TICK,
        "last_result": _last,
    }
