"""
HAWK Audit Log — Kapsamlı denetim kaydı sistemi.

GDPR/KVKK uyumluluğu ve güvenlik denetimi için tüm kritik
platform eylemlerini kayıt altına alır.

Kayıt türleri:
  - auth:      Giriş, çıkış, kayıt, şifre sıfırlama
  - api:       API key oluşturma/silme
  - data:      Kullanıcı verisi okuma/yazma
  - payment:   Ödeme ve wallet işlemleri
  - security:  Güvenlik olayları (rate limit, engellemeler)
  - admin:     Operatör eylemleri
  - agent:     Ajan görev yürütmeleri
  - system:    Platform sistem olayları
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import deque
from typing import Any, Dict, List, Optional

_log = logging.getLogger("hawk.audit")

# ─────────────────────────── IN-MEMORY BUFFER ──────────────────────

_AUDIT_BUFFER: deque = deque(maxlen=2000)

ACTION_AUTH     = "auth"
ACTION_API      = "api"
ACTION_DATA     = "data"
ACTION_PAYMENT  = "payment"
ACTION_SECURITY = "security"
ACTION_ADMIN    = "admin"
ACTION_AGENT    = "agent"
ACTION_SYSTEM   = "system"
ACTION_CHAT     = "chat"

SEVERITY_LOW    = 1
SEVERITY_MEDIUM = 5
SEVERITY_HIGH   = 8
SEVERITY_CRITICAL = 10


# ─────────────────────────── AUDIT RECORD ──────────────────────────

def _build_record(
    action: str,
    actor: str,
    resource: str,
    detail: str,
    *,
    severity: int = SEVERITY_LOW,
    ip: str = "unknown",
    extra: Optional[Dict] = None,
    success: bool = True,
) -> Dict[str, Any]:
    return {
        "ts": time.time(),
        "action": action,
        "actor": actor[:100],
        "resource": resource[:100],
        "detail": detail[:300],
        "severity": severity,
        "ip": ip[:45],
        "success": success,
        "extra": extra or {},
    }


# ─────────────────────────── AUDIT LOGGER ──────────────────────────

class AuditLog:
    """
    HAWK merkezi denetim kayıt sistemi.
    Hem in-memory hem PostgreSQL'e yazar.
    """

    @classmethod
    def record(
        cls,
        action: str,
        actor: str,
        resource: str,
        detail: str,
        *,
        severity: int = SEVERITY_LOW,
        ip: str = "unknown",
        extra: Optional[Dict] = None,
        success: bool = True,
    ):
        """Sync kayıt — in-memory buffer'a yazar."""
        rec = _build_record(action, actor, resource, detail,
                            severity=severity, ip=ip, extra=extra, success=success)
        _AUDIT_BUFFER.append(rec)
        if severity >= SEVERITY_HIGH:
            _log.warning("AUDIT HIGH severity=%d action=%s actor=%s resource=%s", severity, action, actor, resource)

    @classmethod
    async def arecord(
        cls,
        action: str,
        actor: str,
        resource: str,
        detail: str,
        *,
        severity: int = SEVERITY_LOW,
        ip: str = "unknown",
        extra: Optional[Dict] = None,
        success: bool = True,
    ):
        """Async kayıt — buffer + fire-and-forget PostgreSQL."""
        rec = _build_record(action, actor, resource, detail,
                            severity=severity, ip=ip, extra=extra, success=success)
        _AUDIT_BUFFER.append(rec)
        if severity >= SEVERITY_HIGH:
            _log.warning("AUDIT HIGH severity=%d action=%s actor=%s resource=%s", severity, action, actor, resource)
        asyncio.create_task(cls._persist(rec))

    @staticmethod
    async def _persist(rec: Dict):
        """PostgreSQL'e asenkron yaz — hata olursa sessizce geç."""
        try:
            from core.pg_memory import _get_pool
            pool = await _get_pool()
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO hawk_audit_log
                        (action, actor, resource, detail, severity, ip, success, extra, created_at)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,NOW())
                    """,
                    rec["action"], rec["actor"], rec["resource"], rec["detail"][:300],
                    rec["severity"], rec["ip"], rec["success"],
                    json.dumps(rec["extra"], ensure_ascii=False)[:1000],
                )
        except Exception:
            pass

    @classmethod
    def recent(
        cls,
        limit: int = 50,
        action: Optional[str] = None,
        min_severity: int = 0,
    ) -> List[Dict]:
        events = list(_AUDIT_BUFFER)
        if action:
            events = [e for e in events if e["action"] == action]
        if min_severity > 0:
            events = [e for e in events if e["severity"] >= min_severity]
        return sorted(events, key=lambda x: x["ts"], reverse=True)[:limit]

    @classmethod
    def stats(cls) -> Dict[str, Any]:
        events = list(_AUDIT_BUFFER)
        action_counts: Dict[str, int] = {}
        severity_counts: Dict[str, int] = {"low": 0, "medium": 0, "high": 0, "critical": 0}
        failures = 0

        for e in events:
            action_counts[e["action"]] = action_counts.get(e["action"], 0) + 1
            s = e["severity"]
            if s >= SEVERITY_CRITICAL:
                severity_counts["critical"] += 1
            elif s >= SEVERITY_HIGH:
                severity_counts["high"] += 1
            elif s >= SEVERITY_MEDIUM:
                severity_counts["medium"] += 1
            else:
                severity_counts["low"] += 1
            if not e.get("success"):
                failures += 1

        return {
            "total": len(events),
            "by_action": action_counts,
            "by_severity": severity_counts,
            "failures": failures,
            "buffer_size": len(_AUDIT_BUFFER),
        }


# ─────────────────────────── CONVENIENCE HELPERS ───────────────────

def audit_auth(actor: str, detail: str, *, ip: str = "unknown", success: bool = True, severity: int = SEVERITY_LOW):
    AuditLog.record(ACTION_AUTH, actor, "auth", detail, ip=ip, success=success, severity=severity)

def audit_payment(actor: str, detail: str, *, ip: str = "unknown", extra: Optional[Dict] = None):
    AuditLog.record(ACTION_PAYMENT, actor, "wallet", detail, ip=ip, severity=SEVERITY_MEDIUM, extra=extra)

def audit_security(actor: str, detail: str, *, ip: str = "unknown", severity: int = SEVERITY_HIGH):
    AuditLog.record(ACTION_SECURITY, actor, "security", detail, ip=ip, severity=severity, success=False)

def audit_admin(actor: str, resource: str, detail: str, *, ip: str = "unknown"):
    AuditLog.record(ACTION_ADMIN, actor, resource, detail, ip=ip, severity=SEVERITY_MEDIUM)

async def audit_agent(actor: str, role: str, goal: str, *, success: bool = True):
    await AuditLog.arecord(ACTION_AGENT, actor, f"agent:{role}", goal[:200], success=success)


# ─────────────────────────── DB MIGRATION ──────────────────────────

async def ensure_audit_table():
    """DB tablosunu oluştur — startup'ta çağrılır."""
    try:
        from core.pg_memory import _get_pool
        pool = await _get_pool()
        async with pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS hawk_audit_log (
                    id          BIGSERIAL PRIMARY KEY,
                    action      TEXT NOT NULL,
                    actor       TEXT NOT NULL,
                    resource    TEXT NOT NULL DEFAULT '',
                    detail      TEXT NOT NULL DEFAULT '',
                    severity    INTEGER NOT NULL DEFAULT 1,
                    ip          TEXT NOT NULL DEFAULT 'unknown',
                    success     BOOLEAN NOT NULL DEFAULT TRUE,
                    extra       JSONB,
                    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_hawk_audit_created ON hawk_audit_log(created_at DESC)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_hawk_audit_actor ON hawk_audit_log(actor)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_hawk_audit_severity ON hawk_audit_log(severity)"
            )
    except Exception as e:
        _log.warning("audit_table init failed: %s", e)


audit_log = AuditLog()
