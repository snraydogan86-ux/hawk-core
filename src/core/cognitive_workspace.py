"""
FAZ 17 — Küresel bilişsel çalışma alanı (global workspace; MEKANİK).

Modüller (memory, reasoning, agents, self-model, research, ops) ortak bir çalışma alanına
salience'lı içerik YAYINLAR; kapasite sınırlıdır → en yüksek-öncelik N içerik tüm abonelere
görünür (broadcast). Global Workspace Theory'den ESİNLİ ama tamamen mühendislik: öncelik
kuyruğu + zamanla sönümlenme + provenance. KESİN YASAK: bilinç/farkındalık İDDİASI YOK.
"""
from __future__ import annotations
import time
import hashlib

_CAPACITY = 32          # workspace kapasitesi (dikkat sınırı)
_DECAY_HALFLIFE_S = 120.0
_WORKSPACE: dict[str, dict] = {}   # id -> item


def _now() -> float:
    return time.time()


def _decayed_salience(item: dict, now: float) -> float:
    age = max(0.0, now - item["ts"])
    import math
    return item["salience"] * (0.5 ** (age / _DECAY_HALFLIFE_S))


def publish(source: str, kind: str, content, *, salience: float = 0.5,
            tags: list | None = None, ttl_s: float = 300.0) -> str:
    """Bir modül içerik yayınlar (provenance + salience ile). Kapasite aşılırsa en zayıf düşer."""
    now = _now()
    key = hashlib.sha256(f"{source}:{kind}:{str(content)[:80]}".encode()).hexdigest()[:16]
    _WORKSPACE[key] = {"id": key, "source": source, "kind": kind, "content": content,
                       "salience": max(0.0, min(1.0, float(salience))), "tags": tags or [],
                       "ts": now, "expires": now + ttl_s}
    _evict(now)
    return key


def _evict(now: float) -> None:
    # süresi geçenleri at
    for k in [k for k, v in _WORKSPACE.items() if v["expires"] <= now]:
        _WORKSPACE.pop(k, None)
    # kapasite aşımı → en düşük sönümlü-salience'ları at
    if len(_WORKSPACE) > _CAPACITY:
        ranked = sorted(_WORKSPACE.values(), key=lambda i: _decayed_salience(i, now))
        for i in ranked[: len(_WORKSPACE) - _CAPACITY]:
            _WORKSPACE.pop(i["id"], None)


def broadcast(top_k: int = 3) -> list:
    """Dikkat kazananları döndür — en yüksek sönümlü-salience'lı içerikler (tüm abonelere görünür)."""
    now = _now()
    _evict(now)
    items = sorted(_WORKSPACE.values(), key=lambda i: _decayed_salience(i, now), reverse=True)
    out = []
    for i in items[:max(0, top_k)]:
        out.append({"id": i["id"], "source": i["source"], "kind": i["kind"],
                    "content": i["content"], "salience": round(_decayed_salience(i, now), 4),
                    "tags": i["tags"]})
    return out


def state() -> dict:
    now = _now()
    _evict(now)
    return {"size": len(_WORKSPACE), "capacity": _CAPACITY,
            "broadcast": broadcast(3),
            "sources": sorted({i["source"] for i in _WORKSPACE.values()})}


def clear() -> None:
    _WORKSPACE.clear()


async def populate_from_modules() -> dict:
    """Çapraz-modül entegrasyon: self-model uyarıları + araştırma problemleri + ops sağlığı
    workspace'e yayınlanır → en önemli bilgi broadcast'te belirir (mekanik dikkat)."""
    published = []
    # 1) öz-model sağlık uyarıları (yüksek salience — sistem riski)
    try:
        from core import self_model as _sm
        snap = await _sm.snapshot(components=[])
        for w in snap.get("self_warnings", []):
            published.append(publish("self_model", "health_warning", w, salience=0.9, tags=["risk"]))
    except Exception:
        pass
    # 2) ops kaynak anlık görüntüsü (orta salience)
    try:
        from core import ops_monitor as _ops
        h = _ops.resource_snapshot()
        published.append(publish("ops_monitor", "resource", {"disk_pct": h.get("disk_pct"),
                         "mem_pct": h.get("mem_pct")}, salience=0.5, tags=["health"]))
    except Exception:
        pass
    # 3) açık araştırma problemleri (varsa)
    try:
        from core.pg_memory import _get_pool
        pool = await _get_pool()
        async with pool.acquire() as c:
            reg = await c.fetchval("SELECT to_regclass('public.hawk_problems')")
            if reg:
                rows = await c.fetch("SELECT title,priority FROM hawk_problems WHERE status='open' ORDER BY priority DESC LIMIT 3")
                for r in rows:
                    published.append(publish("research_loop", "open_problem", r["title"],
                                     salience=min(1.0, 0.4 + 0.1 * float(r["priority"] or 0)), tags=["research"]))
    except Exception:
        pass
    return {"published": len(published), "state": state()}
