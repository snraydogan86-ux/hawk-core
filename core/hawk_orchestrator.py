"""
HAWK Orkestratör — HAWK'ın beynini TÜM sistemine bağlar + kendi ajanını üretip görevlendirir.

Yetenekler (HAWK'ın beynine araç olarak bağlanır):
  system_map()                 — HAWK'ın TÜM altyapısı: servisler, endpoint'ler, DB tabloları,
                                 zamanlanmış görevler, ajan profilleri, swarm durumu. (Tam farkındalık)
  spawn_agent(task, profile)   — HAWK bir alt-görev için KENDİ ajanını üretip çalıştırır (delegasyon).
  plan_and_run(goal)           — HAWK büyük hedefi alt-görevlere böler + her birine ajan atar + birleştirir.

Güvenlik: spawn derinliği sınırlı (sonsuz üreme yok). Kritik araçlar yine onay-gate'li.
"""
from __future__ import annotations

import asyncio
import contextvars
import logging
from typing import Any, Dict, List

_log = logging.getLogger("hawk.orchestrator")

# Sonsuz ajan-üreme koruması: spawn derinliği
_spawn_depth: contextvars.ContextVar[int] = contextvars.ContextVar("hawk_spawn_depth", default=0)
_MAX_SPAWN_DEPTH = 2


async def system_map() -> str:
    """HAWK'ın tüm sistemini özetler — beynine 'kendini bil' farkındalığı verir."""
    parts: List[str] = []

    # 1) Servisler (docker)
    try:
        import httpx
        async with httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(uds="/var/run/docker.sock"),
                                     timeout=8.0) as c:
            r = await c.get("http://localhost/containers/json")
            if r.status_code == 200:
                svc = [f"{d['Names'][0].lstrip('/')}({d['State']})" for d in r.json()]
                parts.append("SERVİSLER: " + ", ".join(svc))
    except Exception as e:
        parts.append(f"SERVİSLER: okunamadı ({str(e)[:40]})")

    # 2) DB tabloları
    try:
        from core.pg_memory import _get_pool
        pool = await _get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")
            parts.append(f"DB TABLOLARI ({len(rows)}): " + ", ".join(r["tablename"] for r in rows[:40]))
    except Exception as e:
        parts.append(f"DB: okunamadı ({str(e)[:40]})")

    # 3) Zamanlanmış görevler
    try:
        from core.scheduler import scheduler
        tasks = getattr(scheduler, "_tasks", {}) or {}
        parts.append(f"ZAMANLANMIŞ GÖREVLER ({len(tasks)}): " + ", ".join(list(tasks.keys())[:20]))
    except Exception:
        pass

    # 4) Ajan profilleri
    try:
        from core.agent_loop import AGENT_PROFILES
        parts.append("AJAN PROFİLLERİ: " + ", ".join(AGENT_PROFILES.keys()))
    except Exception:
        pass

    # 5) Swarm
    try:
        from core.swarm import get_swarm
        sw = get_swarm()
        parts.append(f"SWARM: {getattr(sw, 'worker_count', '?')} worker")
    except Exception:
        pass

    # 6) Öğrenilen dersler
    try:
        from core.hawk_learning import stats
        st = await stats()
        parts.append(f"ÖĞRENİLEN DERS: {st.get('total_lessons', 0)} ({st.get('by_tag', {})})")
    except Exception:
        pass

    return "\n".join(parts) if parts else "Sistem haritası çıkarılamadı."


async def spawn_agent(task: str, profile: str = "genel") -> str:
    """HAWK bir alt-görev için kendi ajanını üretip çalıştırır (delegasyon).
    Derinlik sınırı: sonsuz üreme yok."""
    depth = _spawn_depth.get()
    if depth >= _MAX_SPAWN_DEPTH:
        return f"Spawn derinlik sınırı ({_MAX_SPAWN_DEPTH}) — bu görevi kendin yap, alt-ajan üretme."
    token = _spawn_depth.set(depth + 1)
    try:
        from core.agent_loop import run_profiled_agent
        _log.info("spawn_agent depth=%d profile=%s task=%.50s", depth + 1, profile, task)
        r = await asyncio.wait_for(run_profiled_agent(profile, task), timeout=200.0)
        ans = (r.get("answer") or "")[:1200]
        return f"[Alt-ajan '{profile}' sonucu] {ans}"
    except asyncio.TimeoutError:
        return f"Alt-ajan ({profile}) zaman aşımı (200s)."
    except Exception as e:
        return f"Alt-ajan hatası: {str(e)[:150]}"
    finally:
        _spawn_depth.reset(token)
