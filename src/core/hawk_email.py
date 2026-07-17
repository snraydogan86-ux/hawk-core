"""
HAWK Email Utility — Brevo API üzerinden email gönderim modülü.

Kullanım:
  from core.hawk_email import send_email
  await send_email(to="soner@...", subject="Alert", body="...")
"""
from __future__ import annotations

import os
from typing import Optional

import httpx

_BREVO_KEY  = os.getenv("BREVO_API_KEY", "")
_FROM_EMAIL = os.getenv("HAWK_SMTP_FROM", "noreply@hawk-operasyon.com")
_FROM_NAME  = "HAWK AI"
_OWNER_EMAIL = os.getenv("HAWK_OWNER_EMAIL", os.getenv("HAWK_SMTP_FROM", "snraydogan86@gmail.com"))


async def send_email(
    to: str,
    subject: str,
    body: str,
    *,
    html: Optional[str] = None,
    from_email: str = _FROM_EMAIL,
    from_name: str = _FROM_NAME,
) -> bool:
    """Brevo API üzerinden email gönder. Başarılı → True, hata → False."""
    if not _BREVO_KEY:
        return False
    payload: dict = {
        "sender": {"name": from_name, "email": from_email},
        "to": [{"email": to}],
        "subject": subject,
        "textContent": body,
    }
    if html:
        payload["htmlContent"] = html

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.post(
                "https://api.brevo.com/v3/smtp/email",
                json=payload,
                headers={"api-key": _BREVO_KEY, "Accept": "application/json"},
            )
        return r.status_code in (200, 201, 202)
    except Exception:
        return False


async def alert_owner(subject: str, body: str) -> bool:
    """Platform operatörüne (Soner) kritik alert emaili gönder."""
    return await send_email(_OWNER_EMAIL, f"[HAWK ALERT] {subject}", body)
