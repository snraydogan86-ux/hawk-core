"""
HAWK Social Agent — X (Twitter) görünürlük & içerik ajanı.

Bağımsız OAuth 1.0a imzalama (dependency yok). Okuma çalışır; YAZMA (tweet) için
X uygulamasının Read+Write izni + write-yetkili access token gerekir.

GÜVENLİK (Soner direktifi):
  - Onaysız PAYLAŞIM YAPMAZ. post_tweet(approved=False) → taslak döner, atmaz.
  - Gizli anahtar loglanmaz.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import secrets
import time
import urllib.parse as up
from typing import Any, Dict, Optional

import httpx

log = logging.getLogger("hawk.social")

CK = os.getenv("X_API_KEY", "")
CS = os.getenv("X_API_SECRET", "")
AT = os.getenv("X_ACCESS_TOKEN", "")
ATS = os.getenv("X_ACCESS_TOKEN_SECRET", "")


def is_ready() -> bool:
    return bool(CK and CS and AT and ATS)


def _q(s: Any) -> str:
    return up.quote(str(s), safe="~")


def _oauth_header(method: str, url: str, extra: Optional[Dict[str, str]] = None) -> str:
    p = {"oauth_consumer_key": CK, "oauth_nonce": secrets.token_hex(16),
         "oauth_signature_method": "HMAC-SHA1", "oauth_timestamp": str(int(time.time())),
         "oauth_token": AT, "oauth_version": "1.0"}
    allp = dict(p)
    if extra:
        allp.update(extra)
    base = "&".join(f"{_q(k)}={_q(allp[k])}" for k in sorted(allp))
    sigbase = f"{method}&{_q(url)}&{_q(base)}"
    key = f"{_q(CS)}&{_q(ATS)}"
    sig = base64.b64encode(hmac.new(key.encode(), sigbase.encode(), hashlib.sha1).digest()).decode()
    p["oauth_signature"] = sig
    return "OAuth " + ", ".join(f'{_q(k)}="{_q(v)}"' for k, v in p.items())


async def x_verify() -> Dict[str, Any]:
    """Hesabı doğrula + temel bilgi (okuma)."""
    if not is_ready():
        return {"ok": False, "error": "x_credentials_missing"}
    url = "https://api.twitter.com/2/users/me"
    extra = {"user.fields": "public_metrics,description,created_at"}
    full = url + "?" + up.urlencode(extra)
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(full, headers={"Authorization": _oauth_header("GET", url, extra)})
        if r.status_code == 200:
            d = r.json().get("data", {})
            return {"ok": True, "account": d}
        return {"ok": False, "http": r.status_code, "detail": r.text[:200]}
    except Exception as e:
        return {"ok": False, "error": str(e)[:120]}


async def suggest_content(topic: str = "", n: int = 3) -> Dict[str, Any]:
    """the serving engine ile içerik (tweet) önerileri üret — PAYLAŞMAZ, sadece taslak."""
    try:
        from core.hawk_core.engine import brain_chat
        prompt = (
            f"Sen HAWK AI'sin (BSC token + AI işletim sistemi, @HAWKAIcoinkp). "
            f"X için {n} adet kısa, etkileyici tweet önerisi yaz (her biri <280 karakter, 2-3 hashtag). "
            + (f"Konu: {topic}. " if topic else "") +
            "Sadece tweet'leri madde madde ver."
        )
        r = await brain_chat([{"role": "user", "content": prompt}], max_tokens=400, temperature=0.7)
        return {"ok": True, "suggestions": (r.get("text") or "").strip()}
    except Exception as e:
        return {"ok": False, "error": str(e)[:120]}


async def post_tweet(text: str, *, approved: bool = False) -> Dict[str, Any]:
    """Tweet at — SADECE onaylıysa. Onaysız taslak döner (Soner direktifi: onaysız paylaşım yok)."""
    if not approved:
        return {"ok": False, "blocked": True,
                "reason": "Soner direktifi: paylaşım için onay gerekir.", "draft": text[:280]}
    if not is_ready():
        return {"ok": False, "error": "x_credentials_missing"}
    if len(text) > 280:
        text = text[:277] + "..."
    url = "https://api.twitter.com/2/tweets"
    try:
        async with httpx.AsyncClient(timeout=25) as c:
            r = await c.post(url, headers={"Authorization": _oauth_header("POST", url),
                                           "Content-Type": "application/json"}, json={"text": text})
        if r.status_code in (200, 201):
            tid = r.json()["data"]["id"]
            return {"ok": True, "id": tid, "url": f"https://twitter.com/HAWKAIcoinkp/status/{tid}"}
        # 403 = app Read-only; write izni + token yenileme gerekir
        return {"ok": False, "http": r.status_code, "detail": r.text[:250],
                "hint": "403 ise X app izni Read+Write yapilip access token YENIDEN uretilmeli."}
    except Exception as e:
        return {"ok": False, "error": str(e)[:120]}
