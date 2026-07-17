"""
FAZ 10 — Halktan izinli öğrenme: kullanıcı eğitim-veri consent kaydı.

KESİN YASAK: consent=granted OLMADAN kullanıcı verisi dataset adayına GİRMEZ. Kullanıcı
izni geri çekerse (veya 'unut' derse) ilgili adaylar revoke edilir (B8). Düz email/id saklanmaz.
"""
from __future__ import annotations
import hashlib
from core.pg_memory import _get_pool


def user_hash(user_key: str) -> str:
    return hashlib.sha256(("hawk-src:" + str(user_key or "")).encode()).hexdigest()  # B8 ile AYNI


async def set_consent(user_key: str, granted: bool, scope: str = "training") -> dict:
    h = user_hash(user_key)
    pool = await _get_pool()
    async with pool.acquire() as c:
        await c.execute(
            """INSERT INTO hawk_user_consent (user_hash,granted,scope,updated_at)
               VALUES ($1,$2,$3,now())
               ON CONFLICT (user_hash) DO UPDATE SET granted=EXCLUDED.granted,
                 scope=EXCLUDED.scope, updated_at=now()""",
            h, bool(granted), scope)
    # izin geri çekilirse → ilgili eğitim adaylarını da revoke et (B8)
    if not granted:
        try:
            from core.model_family import store_pg as _sp
            await _sp.revoke_candidates_for_user(user_key)
        except Exception:
            pass
    return {"ok": True, "granted": granted, "scope": scope}


async def has_consent(user_key: str) -> bool:
    h = user_hash(user_key)
    pool = await _get_pool()
    async with pool.acquire() as c:
        v = await c.fetchval("SELECT granted FROM hawk_user_consent WHERE user_hash=$1", h)
    return bool(v)


async def capture_if_consented(user_key: str, *, source_type: str, objective: str,
                               output_text: str, review_passed: bool = True) -> dict:
    """Kullanıcı etkileşimini SADECE consent varsa dataset adayına öner (redakte, pending).
    consent yok → HİÇBİR ŞEY yapmaz (KESİN YASAK)."""
    if not await has_consent(user_key):
        return {"ok": False, "reason": "no_consent"}
    from core.model_family.self_improve_flow import propose_from_task
    return await propose_from_task(user_scope=user_key, source_type=source_type,
                                   objective=objective, output_text=output_text,
                                   review_passed=review_passed)
