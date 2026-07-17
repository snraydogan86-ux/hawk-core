import os
import hmac
import base64
import hashlib
import time
from typing import Optional, Dict, Any

SECRET_KEY = os.getenv("HAWK_AUTH_SECRET", "hawk-super-secret-change-me")

_ACCESS_TTL  = 3600        # 1 saat
_REFRESH_TTL = 2592000     # 30 gün

def hash_password(password: str) -> str:
    if not isinstance(password, str) or not password:
        raise ValueError("invalid password")
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100000)
    return base64.b64encode(salt + digest).decode("utf-8")

def verify_password(password: str, stored: str) -> bool:
    try:
        raw = base64.b64decode(stored.encode("utf-8"))
        salt, saved = raw[:16], raw[16:]
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100000)
        return hmac.compare_digest(saved, digest)
    except Exception:
        return False

def create_token(user_id: int, email: str) -> str:
    """JWT access token — 1 saat geçerli."""
    import jwt as _pyjwt
    now = int(time.time())
    payload = {
        "sub": str(user_id),
        "email": email,
        "typ": "access",
        "iat": now,
        "exp": now + _ACCESS_TTL,
    }
    return _pyjwt.encode(payload, SECRET_KEY, algorithm="HS256")

def create_refresh_token(user_id: int, email: str) -> str:
    """JWT refresh token — 30 gün geçerli."""
    import jwt as _pyjwt
    now = int(time.time())
    payload = {
        "sub": str(user_id),
        "email": email,
        "typ": "refresh",
        "iat": now,
        "exp": now + _REFRESH_TTL,
    }
    return _pyjwt.encode(payload, SECRET_KEY, algorithm="HS256")

def verify_token(token: str) -> Optional[Dict[str, Any]]:
    """
    1. Önce JWT dene (yeni format, exp kontrolü dahil).
    2. Başarısız olursa eski HMAC base64 formatını dene (geçiş dönemi).
    3. Her ikisi de başarısız: None.
    """
    # --- Yol 1: JWT ---
    try:
        import jwt as _pyjwt
        data = _pyjwt.decode(token, SECRET_KEY, algorithms=["HS256"])
        if data.get("typ") == "refresh":
            # Refresh token'ı access gibi kullanmayı engelle
            return None
        user_id = int(data["sub"])
        email = str(data["email"])
        return {"user_id": user_id, "email": email}
    except Exception:
        pass

    # --- Yol 2: Eski HMAC base64 format (backward compat) ---
    try:
        raw = base64.b64decode(token.encode("utf-8")).decode("utf-8")
        parts = raw.split(":")
        if len(parts) < 3:
            return None
        user_id = int(parts[0])
        email = parts[1]
        sig = parts[2]
        payload_str = f"{user_id}:{email}"
        expected = hmac.new(
            SECRET_KEY.encode("utf-8"),
            payload_str.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        return {"user_id": user_id, "email": email}
    except Exception:
        return None

def verify_refresh_token(token: str) -> Optional[Dict[str, Any]]:
    """Refresh token doğrular; access token'ı reddeder."""
    try:
        import jwt as _pyjwt
        data = _pyjwt.decode(token, SECRET_KEY, algorithms=["HS256"])
        if data.get("typ") != "refresh":
            return None
        return {"user_id": int(data["sub"]), "email": str(data["email"])}
    except Exception:
        return None
