"""
HAWK Memory Write Pipeline — TEK merkezi uzun-süreli hafıza yazma yolu.

Hiçbir endpoint doğrudan store çağırmaz; hepsi buradan geçer:
  write_message_facts(user_id, message, conversation_id, extra_facts)
     → extract_facts (saf)
     → write_fact  → 1) SQLite (kaynak, kritik)  2) PostgreSQL  3) Supabase

Kurallar:
 - SQLite ANA KAYNAK: yazamıyorsa kritik hata (yine de mirror'lar bloklanmaz).
 - SQLite confidence-guard bir yazımı reddederse (düşük güven) → mirror'lar da
   YAZILMAZ (tutarlılık); "skip" döner (başarı sayılır).
 - Postgres/Supabase eventual-consistency: başarısızsa write_retry kuyruğuna girer.
 - IDEMPOTENT: SQLite/PG ON CONFLICT DO UPDATE; Supabase (user_ref,category) tekil.
 - Hiçbir store sessizce hata yutmaz — her store sonucu ayrı raporlanır.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List

from core import memory_store as _MS


async def write_fact(user_id: str, key: str, value: str, *, confidence: float = 0.9,
                     source: str = "pattern", conversation_id: str = "",
                     extraction_method: str = "pattern") -> Dict[str, Any]:
    res: Dict[str, Any] = {"sqlite": None, "postgres": None, "supabase": None}

    # 1) SQLite — kaynak + confidence-guard
    try:
        wrote = await asyncio.to_thread(
            _MS.remember_fact, key, value, user_id, confidence, source,
            conversation_id, extraction_method)
        res["sqlite"] = True
    except Exception:
        res["sqlite"] = False
        return res  # kritik: kaynak yazılamadı
    if not wrote:
        # confidence-guard reddetti / boş → mirror'lar değişmemeli (tutarlı)
        res["postgres"] = "skip"; res["supabase"] = "skip"
        return res

    # 2) PostgreSQL
    try:
        from core import pg_memory as _PG
        await _PG.pg_remember(user_id, key, value)
        res["postgres"] = True
    except Exception:
        res["postgres"] = False
        _MS.queue_write_retry(user_id, key, value, confidence, source,
                              conversation_id, extraction_method, "postgres")

    # 3) Supabase (idempotent upsert)
    try:
        from core import supabase_store as _SB
        if _SB.is_ready():
            ok = await _SB.upsert_user_memory(
                user_id, category=key, fact=value,
                metadata={"key": key, "conversation_id": conversation_id})
            res["supabase"] = bool(ok)
            if not ok:
                _MS.queue_write_retry(user_id, key, value, confidence, source,
                                      conversation_id, extraction_method, "supabase")
        else:
            res["supabase"] = "inactive"
    except Exception:
        res["supabase"] = False
        _MS.queue_write_retry(user_id, key, value, confidence, source,
                              conversation_id, extraction_method, "supabase")
    return res


async def write_facts(user_id: str, facts: List[Dict], conversation_id: str = "") -> Dict[str, Any]:
    agg = {"count": 0, "sqlite": 0, "postgres": 0, "supabase": 0, "queued": 0, "per_fact": []}
    for f in facts or []:
        r = await write_fact(user_id, f["key"], f["value"],
                             confidence=f.get("confidence", 0.9), source=f.get("source", "pattern"),
                             conversation_id=f.get("conversation_id", conversation_id),
                             extraction_method=f.get("extraction_method", "pattern"))
        agg["count"] += 1
        if r.get("sqlite") is True:
            agg["sqlite"] += 1
        if r.get("postgres") is True:
            agg["postgres"] += 1
        if r.get("supabase") is True:
            agg["supabase"] += 1
        if r.get("postgres") is False or r.get("supabase") is False:
            agg["queued"] += 1
        agg["per_fact"].append({f["key"]: r})
    return agg


def _scope(user_id: str, project_id=None) -> str:
    """project_id verilirse USER hafızasından AYRI, per-(user,project) namespace.
    Böylece: cross-project izolasyon + 'adımı unut' (user scope) project'i silmez."""
    if project_id:
        return f"{user_id}#proj#{str(project_id)}"
    return user_id


async def write_message_facts(user_id: str, message: str, conversation_id: str = "",
                              extra_facts=None, project_id=None) -> Dict[str, Any]:
    """TEK giriş noktası — chat/chat-stream/voice/workspace/agent hepsi bunu çağırır.
    project_id verilirse fact'ler PROJE hafızasına (ayrı scope) yazılır (backward-compatible)."""
    facts = _MS.extract_facts(str(message or ""), conversation_id=conversation_id,
                              extra_facts=extra_facts)
    return await write_facts(_scope(user_id, project_id), facts, conversation_id=conversation_id)


async def retry_pending_writes(max_items: int = 50) -> int:
    """write_retry kuyruğundaki başarısız pg/supabase yazımlarını idempotent tekrar dener."""
    done = 0
    try:
        pend = _MS.list_pending_writes()[:max_items]
        for p in pend:
            store = p.get("store"); ok = False
            try:
                if store == "postgres":
                    from core import pg_memory as _PG
                    await _PG.pg_remember(p["user_id"], p["key"], p["value"]); ok = True
                elif store == "supabase":
                    from core import supabase_store as _SB
                    if _SB.is_ready():
                        ok = bool(await _SB.upsert_user_memory(
                            p["user_id"], category=p["key"], fact=p["value"],
                            metadata={"key": p["key"], "conversation_id": p.get("conversation_id", "")}))
                    else:
                        ok = False
            except Exception:
                ok = False
            if ok:
                _MS.clear_write_retry(p["rowid"]); done += 1
            else:
                _MS.bump_write_attempt(p["rowid"])
    except Exception:
        pass
    return done
