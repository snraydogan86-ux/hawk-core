"""
HAWK Identity Guard — kimlik YALNIZCA doğrulanmış bearer/JWT'den alınır.

Güvenlik ilkesi (F1/F2 — P0 IDOR + kota/plan spoof fix):
  - Request body/header'daki email, user_id, session_id, owner_id vb. HİÇBİR ALAN
    kimlik kaynağı DEĞİLDİR. (Eskiden bunlara güveniliyordu → IDOR.)
  - Authenticated kimlik yalnızca _get_user_from_bearer sonucundan gelir.
  - Guest kimliği: sunucu tarafından üretilen, HMAC ile imzalı, istemcinin
    değiştiremeyeceği bir guest ID (imzalı çerez) + güvenilir IP sinyali.
  - Guest anahtarları 'guest:'/'gq:' ile namespace'lidir → HİÇBİR zaman gerçek
    bir hesabın email anahtarına eşit olamaz (guest, kayıtlı kullanıcının
    hafıza/geçmiş/plan/kotasına bağlanamaz).
  - Kota anahtarı IP'ye sabitlenir → email/session_id/çerez değiştirerek
    kota sıfırlanamaz (rotation-proof).
"""

import hashlib
import hmac
import os
import secrets

_SECRET = (
    os.getenv("HAWK_AUTH_SECRET")
    or os.getenv("HAWK_ADMIN_KEY")
    or "hawk-identity-guard-fallback-change-me"
).encode()

GUEST_COOKIE = "hawk_gid"


def _mac(msg: str) -> str:
    return hmac.new(_SECRET, msg.encode("utf-8", "replace"), hashlib.sha256).hexdigest()


def trusted_client_ip(request) -> str:
    """Güvenilir istemci IP'si. Uygulama Caddy reverse-proxy ARKASINDA; Caddy
    X-Forwarded-For'u kendisi set eder, bu yüzden en soldaki değer Caddy'nin
    gördüğü gerçek istemcidir. Doğrudan bağlantı yoksa client.host'a düşer.
    (İstemcinin uydurduğu XFF'e kimlik için değil, yalnızca kaba IP sinyali
    olarak bakılır; kota IP-HMAC ile anonimleştirilir.)"""
    try:
        xff = request.headers.get("x-forwarded-for") or ""
        if xff:
            first = xff.split(",")[0].strip()
            if first:
                return first
        xr = (request.headers.get("x-real-ip") or "").strip()
        if xr:
            return xr
        return str(getattr(getattr(request, "client", None), "host", "") or "")
    except Exception:
        return ""


def _ip_anchor(request) -> str:
    ip = trusted_client_ip(request) or "noip"
    return _mac("ip:" + ip)[:24]


# ---- imzalı guest çerezi ----

def sign_gid(gid: str) -> str:
    return f"{gid}.{_mac('gid:' + gid)[:24]}"


def verify_gid(token):
    try:
        gid, sig = str(token or "").rsplit(".", 1)
    except ValueError:
        return None
    if gid and hmac.compare_digest(sig, _mac("gid:" + gid)[:24]):
        return gid
    return None


def read_guest_cookie(request):
    try:
        tok = request.cookies.get(GUEST_COOKIE)
    except Exception:
        tok = None
    return verify_gid(tok) if tok else None


def new_guest_cookie():
    """(gid, imzalı_çerez_değeri) — sunucu üretir, istemci değiştiremez."""
    gid = secrets.token_hex(16)
    return gid, sign_gid(gid)


# ---- anahtarlar ----

def guest_memory_key(request) -> str:
    """Guest hafıza/kimlik anahtarı: imzalı çerez varsa ondan, yoksa IP-HMAC.
    Her zaman 'guest:' namespace'i → gerçek email anahtarıyla ASLA çakışmaz."""
    gid = read_guest_cookie(request)
    if gid:
        return "guest:" + gid
    return "guest:" + _ip_anchor(request)


def guest_quota_key(request) -> str:
    """Guest kota anahtarı: DAİMA IP-HMAC → çerez/session/email değiştirerek
    sıfırlanamaz. 'gq:' namespace'i."""
    return "gq:" + _ip_anchor(request)


async def resolve_identity(request, get_user_from_bearer):
    """Kimliği YALNIZCA bearer'dan çöz. Body/header ASLA kimlik kaynağı değil.
    Döner: {authed, email, memory_key, quota_key}."""
    auth = None
    try:
        auth = request.headers.get("authorization")
    except Exception:
        auth = None
    row = None
    try:
        row = await get_user_from_bearer(auth)
    except Exception:
        row = None
    if row and row.get("email"):
        email = str(row.get("email")).strip().lower()
        return {"authed": True, "email": email, "memory_key": email, "quota_key": email}
    return {
        "authed": False,
        "email": None,
        "memory_key": guest_memory_key(request),
        "quota_key": guest_quota_key(request),
    }
