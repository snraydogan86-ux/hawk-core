"""
HAWK imzalı indirme URL'leri (F4).

/api/dl/{fname} artık kalıcı-anonim erişime kapalı. İndirme yalnızca kısa-süreli,
DOSYA-KAPSAMLI, HMAC-imzalı ve süreli bir bağlantı ile mümkün:
  /api/dl/<fname>?exp=<unix>&sig=<hmac>&scope=<owner|"">

- İmza dosya adına + exp'e + scope'a bağlıdır → başka dosyaya/süreye uyarlanamaz.
- Süresi geçmiş, değiştirilmiş veya başka dosyaya kopyalanmış imza REDDEDİLİR.
- Karşılaştırma constant-time (hmac.compare_digest).
- Path traversal koruması endpoint'te korunur (bu modül imza katmanı).
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from typing import Tuple

_SECRET = (
    os.getenv("HAWK_AUTH_SECRET")
    or os.getenv("HAWK_ADMIN_KEY")
    or "hawk-dl-sign-fallback-change-me"
).encode()

PUBLIC_BASE = (os.getenv("HAWK_PUBLIC_BASE") or "https://www.hawk-operasyon.com").rstrip("/")

# Varsayılan TTL'ler (saniye) — güvenli-kısa.
DEFAULT_TTL = int(os.getenv("HAWK_DL_TTL_SECONDS", "86400") or 86400)          # kullanıcı dosyası: 24s
ADMIN_ARTIFACT_TTL = int(os.getenv("HAWK_ARTIFACT_TTL_SECONDS", "604800") or 604800)  # teslim: 7 gün
MAX_TTL = int(os.getenv("HAWK_DL_MAX_TTL_SECONDS", "2592000") or 2592000)       # tavan: 30 gün


def _sig(fname: str, exp: int, scope: str) -> str:
    msg = f"dl:{fname}:{int(exp)}:{scope or ''}"
    return hmac.new(_SECRET, msg.encode("utf-8", "replace"), hashlib.sha256).hexdigest()[:40]


def make_token(fname: str, ttl: int = DEFAULT_TTL, scope: str = "") -> Tuple[int, str]:
    ttl = max(60, min(int(ttl or DEFAULT_TTL), MAX_TTL))
    exp = int(time.time()) + ttl
    return exp, _sig(fname, exp, scope)


def signed_path(fname: str, ttl: int = DEFAULT_TTL, scope: str = "") -> str:
    exp, sig = make_token(fname, ttl=ttl, scope=scope)
    q = f"exp={exp}&sig={sig}"
    if scope:
        q += f"&scope={scope}"
    return f"/api/dl/{fname}?{q}"


def signed_url(fname: str, ttl: int = DEFAULT_TTL, scope: str = "") -> str:
    return f"{PUBLIC_BASE}{signed_path(fname, ttl=ttl, scope=scope)}"


def verify(fname: str, exp, sig: str, scope: str = "") -> Tuple[bool, str]:
    """Döner: (ok, reason). Süre/imza/dosya uyuşmazlığı → (False, sebep)."""
    try:
        exp_i = int(exp)
    except Exception:
        return False, "bad_exp"
    if exp_i < int(time.time()):
        return False, "expired"
    good = _sig(fname, exp_i, scope or "")
    if not hmac.compare_digest(str(sig or ""), good):
        return False, "bad_sig"
    return True, "ok"
