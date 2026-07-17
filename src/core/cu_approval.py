"""
Computer-Use onay + kanonik görev durumu (DB katmanı).

Md.3 — ONAYI GÖREVE KRİPTOGRAFİK OLARAK BAĞLA:
  approval_id, task_id, user_id, tenant_id, device_id, action_type, scope, created_at,
  expires_at, single_use, signature, approval_state.

Onay:
  - başka göreve taşınamaz     (task_id eşleşmesi)
  - başka cihazda kullanılamaz (device_id eşleşmesi)
  - başka kullanıcı kullanamaz (user_id eşleşmesi)
  - süresi dolunca geçersiz     (expires_at)
  - bir kez kullanıldıktan sonra tekrar kullanılamaz (used_at atomik guard)
  - kurcalanamaz                (HMAC signature)

Md.4 — planner/safety/executor AYNI kanonik görev + onay kaydını kullanır (cu_tasks.state).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Optional

from core.pg_memory import _get_pool
from core import cu_task_policy as _P


def _secret() -> bytes:
    # Onay imzalama sırrı: ayrı env, yoksa auth sırrına düş (fail-closed: en az bir sır olmalı).
    s = (os.getenv("HAWK_APPROVAL_SECRET") or os.getenv("HAWK_AUTH_SECRET") or "").strip()
    if not s:
        # Sır tanımsızsa deterministik-olmayan process-sırrı üret (o process ömrü boyunca sabit).
        # Bu, imzasız (dolayısıyla doğrulanamaz) onay üretmeyi engeller.
        s = _PROCESS_FALLBACK_SECRET
    return s.encode("utf-8")


_PROCESS_FALLBACK_SECRET = secrets.token_hex(32)


def _canonical(fields: dict) -> str:
    return "|".join(f"{k}={fields[k]}" for k in sorted(fields.keys()))


def _sign(bound: dict) -> str:
    return hmac.new(_secret(), _canonical(bound).encode("utf-8"), hashlib.sha256).hexdigest()


def _bound_fields(*, approval_id, task_id, user_id, tenant_id, device_id, action_type,
                  scope, nonce_hash) -> dict:
    return {
        "approval_id": str(approval_id), "task_id": str(task_id), "user_id": str(user_id),
        "tenant_id": str(tenant_id or ""), "device_id": str(device_id or ""),
        "action_type": str(action_type), "scope": str(scope or ""), "nonce_hash": str(nonce_hash),
    }


# ── Kanonik görev durumu (cu_tasks) ──────────────────────────────────────────
async def create_task(*, task_id: str, user_id: str, tenant_id: str = "", device_id: str = "",
                       category: str = "", action_type: str = _P.ACT_GENERAL,
                       message: str = "") -> dict:
    pool = await _get_pool()
    async with pool.acquire() as c:
        await c.execute(
            """INSERT INTO cu_tasks (task_id, user_id, tenant_id, device_id, category, action_type,
                                     state, message, transitions)
               VALUES ($1,$2,$3,$4,$5,$6,'PENDING',$7,'[]'::jsonb)
               ON CONFLICT (task_id) DO NOTHING""",
            task_id, str(user_id), str(tenant_id or ""), str(device_id or ""), category,
            action_type, str(message or "")[:2000])
    return {"ok": True, "task_id": task_id, "state": _P.PENDING}


async def get_task(task_id: str) -> Optional[dict]:
    pool = await _get_pool()
    async with pool.acquire() as c:
        row = await c.fetchrow("SELECT * FROM cu_tasks WHERE task_id=$1", task_id)
    return dict(row) if row else None


async def transition(task_id: str, dst: str, *, note: str = "") -> dict:
    """Kanonik durum geçişi — geçersizse REDDEDER (aynı görev hem çalışıp hem reddedilemez).
    Atomik: yalnız mevcut durum geçişe izin veriyorsa uygular."""
    pool = await _get_pool()
    async with pool.acquire() as c:
        async with c.transaction():
            row = await c.fetchrow("SELECT state FROM cu_tasks WHERE task_id=$1 FOR UPDATE", task_id)
            if not row:
                return {"ok": False, "error": "task_not_found"}
            src = str(row["state"])
            if not _P.can_transition(src, dst):
                return {"ok": False, "error": "invalid_transition", "from": src, "to": dst}
            entry = {"from": src, "to": dst, "ts": int(time.time()), "note": note[:120]}
            await c.execute(
                "UPDATE cu_tasks SET state=$1, updated_at=now(), "
                "transitions = transitions || $2::jsonb WHERE task_id=$3",
                dst, json.dumps([entry]), task_id)
    return {"ok": True, "task_id": task_id, "from": src, "to": dst}


# ── Kriptografik onay ────────────────────────────────────────────────────────
async def create_approval(*, task_id: str, user_id: str, device_id: str, action_type: str,
                           tenant_id: str = "", scope: str = "", ttl_seconds: int = 300,
                           single_use: bool = True) -> dict:
    """Görev+kullanıcı+cihaz'a bağlı, süreli, tek-kullanımlık, imzalı onay üret.
    Döner: approval_id + nonce (kullanıcıya gösterilecek jeton). nonce SUNUCUDA saklanmaz (yalnız hash)."""
    approval_id = "cua_" + secrets.token_hex(12)
    nonce = secrets.token_hex(16)
    nonce_hash = hashlib.sha256(nonce.encode("utf-8")).hexdigest()
    bound = _bound_fields(approval_id=approval_id, task_id=task_id, user_id=user_id,
                          tenant_id=tenant_id, device_id=device_id, action_type=action_type,
                          scope=scope, nonce_hash=nonce_hash)
    signature = _sign(bound)
    ttl = max(30, min(int(ttl_seconds or 300), 3600))
    pool = await _get_pool()
    async with pool.acquire() as c:
        await c.execute(
            """INSERT INTO cu_action_approvals
               (approval_id, task_id, user_id, tenant_id, device_id, action_type, scope,
                nonce_hash, signature, approval_state, single_use, expires_at)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,'pending',$10, now() + ($11 || ' seconds')::interval)""",
            approval_id, str(task_id), str(user_id), str(tenant_id or ""), str(device_id or ""),
            str(action_type), str(scope or ""), nonce_hash, signature, bool(single_use), str(ttl))
    return {"ok": True, "approval_id": approval_id, "nonce": nonce, "expires_in": ttl,
            "task_id": task_id, "action_type": action_type}


async def verify_and_consume(*, approval_id: str, task_id: str, user_id: str, device_id: str,
                             action_type: str, nonce: str) -> dict:
    """Onayı doğrula ve TÜKET (tek-kullanımlık). Her uyumsuzluk için belirgin sebep döner.
    Sıra: var → durum → tüketilmemiş → süre → task → user → device → action → nonce → imza."""
    pool = await _get_pool()
    async with pool.acquire() as c:
        async with c.transaction():
            row = await c.fetchrow(
                "SELECT * FROM cu_action_approvals WHERE approval_id=$1 FOR UPDATE", approval_id)
            if not row:
                return {"approved": False, "reason": "not_found"}
            if str(row["approval_state"]) != "pending" or row["used_at"] is not None:
                return {"approved": False, "reason": "already_used"}
            # süre (DB saati)
            expired = await c.fetchval("SELECT now() > $1", row["expires_at"])
            if expired:
                await c.execute("UPDATE cu_action_approvals SET approval_state='expired' WHERE approval_id=$1", approval_id)
                return {"approved": False, "reason": "expired"}
            # bağlı-alan eşleşmeleri (taşınamaz)
            if str(row["task_id"]) != str(task_id):
                return {"approved": False, "reason": "task_mismatch"}
            if str(row["user_id"]) != str(user_id):
                return {"approved": False, "reason": "user_mismatch"}
            if str(row["device_id"] or "") != str(device_id or ""):
                return {"approved": False, "reason": "device_mismatch"}
            if str(row["action_type"]) != str(action_type):
                return {"approved": False, "reason": "action_mismatch"}
            # tek-kullanımlık nonce
            if hashlib.sha256(str(nonce or "").encode("utf-8")).hexdigest() != str(row["nonce_hash"]):
                return {"approved": False, "reason": "bad_nonce"}
            # imza (kurcalama tespiti) — satır alanlarından yeniden hesapla
            bound = _bound_fields(
                approval_id=row["approval_id"], task_id=row["task_id"], user_id=row["user_id"],
                tenant_id=row["tenant_id"], device_id=row["device_id"], action_type=row["action_type"],
                scope=row["scope"], nonce_hash=row["nonce_hash"])
            if not hmac.compare_digest(_sign(bound), str(row["signature"])):
                return {"approved": False, "reason": "tamper"}
            # ATOMİK TÜKET — used_at IS NULL guard (tekrar-kullanım imkânsız / replay reddi)
            consumed = await c.fetchval(
                "UPDATE cu_action_approvals SET approval_state='consumed', used_at=now() "
                "WHERE approval_id=$1 AND used_at IS NULL RETURNING approval_id", approval_id)
            if not consumed:
                return {"approved": False, "reason": "already_used"}
    return {"approved": True, "approval_id": approval_id, "task_id": task_id}


async def reject_approval(approval_id: str) -> dict:
    pool = await _get_pool()
    async with pool.acquire() as c:
        await c.execute(
            "UPDATE cu_action_approvals SET approval_state='rejected' "
            "WHERE approval_id=$1 AND approval_state='pending'", approval_id)
    return {"ok": True, "approval_id": approval_id, "state": "rejected"}
