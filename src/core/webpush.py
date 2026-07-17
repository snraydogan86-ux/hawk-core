"""
HAWK WebPush — VAPID tabanlı push bildirim gönderici.

Subscription endpoint'i kaydeder, bildirim gönderir.
pywebpush veya cryptography ile VAPID imzalama.
"""
from __future__ import annotations

import base64
import json
import os
import struct
import time
from typing import Any, Dict, List, Optional

import httpx

_VAPID_PUBLIC = os.getenv("VAPID_PUBLIC_KEY", "")
_VAPID_PRIVATE = os.getenv("VAPID_PRIVATE_KEY", "")
_VAPID_SUBJECT = os.getenv("VAPID_SUBJECT", "mailto:admin@hawk-operasyon.com")


def vapid_public_key() -> str:
    return _VAPID_PUBLIC


async def send_push(
    subscription: Dict[str, Any],
    *,
    title: str,
    body: str,
    icon: str = "/hawk-icon-192.png?v=2",
    url: str = "/",
    tag: str = "hawk",
) -> bool:
    """
    Web Push API'ye bildirim gönder.
    subscription = {endpoint, keys: {p256dh, auth}}
    """
    if not (_VAPID_PUBLIC and _VAPID_PRIVATE):
        return False

    endpoint = subscription.get("endpoint", "")
    if not endpoint:
        return False

    payload = json.dumps({
        "title": title,
        "body": body,
        "icon": icon,
        "url": url,
        "tag": tag,
    }, ensure_ascii=False).encode()

    try:
        vapid_headers = _build_vapid_headers(endpoint)
        if subscription.get("keys"):
            encrypted = _encrypt_payload(payload, subscription["keys"])
            content_encoding = "aes128gcm"
        else:
            encrypted = payload
            content_encoding = "aesgcm"

        headers = {
            **vapid_headers,
            "Content-Type": "application/octet-stream",
            "Content-Encoding": content_encoding,
            "TTL": "86400",
        }

        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(endpoint, headers=headers, content=encrypted)
        return r.status_code in (200, 201, 202)
    except Exception:
        return False


def _build_vapid_headers(endpoint: str) -> Dict[str, str]:
    """JWT VAPID header'ı — minimal implementation."""
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives import hashes, serialization
    import hmac, hashlib

    # Parse audience from endpoint
    from urllib.parse import urlparse
    parsed = urlparse(endpoint)
    audience = f"{parsed.scheme}://{parsed.netloc}"

    # VAPID JWT
    header = base64.urlsafe_b64encode(
        json.dumps({"typ": "JWT", "alg": "ES256"}).encode()
    ).rstrip(b"=").decode()

    now = int(time.time())
    claims = base64.urlsafe_b64encode(
        json.dumps({"aud": audience, "exp": now + 43200, "sub": _VAPID_SUBJECT}).encode()
    ).rstrip(b"=").decode()

    signing_input = f"{header}.{claims}".encode()

    # Load private key
    priv_bytes = base64.urlsafe_b64decode(_VAPID_PRIVATE + "==")
    priv_int = int.from_bytes(priv_bytes, "big")
    private_key = ec.derive_private_key(priv_int, ec.SECP256R1(), default_backend())

    # Sign
    signature = private_key.sign(signing_input, ec.ECDSA(hashes.SHA256()))
    # DER → raw r||s (64 bytes)
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
    r_val, s_val = decode_dss_signature(signature)
    raw_sig = r_val.to_bytes(32, "big") + s_val.to_bytes(32, "big")
    sig_b64 = base64.urlsafe_b64encode(raw_sig).rstrip(b"=").decode()

    token = f"{header}.{claims}.{sig_b64}"
    return {
        "Authorization": f"vapid t={token},k={_VAPID_PUBLIC}",
    }


def _encrypt_payload(payload: bytes, keys: Dict) -> bytes:
    """aes128gcm ECDH-ES+AES-128-GCM encryption (RFC 8291). Returns encrypted bytes."""
    try:
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.backends import default_backend
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        import os as _os

        p256dh = base64.urlsafe_b64decode(keys["p256dh"] + "==")
        auth_secret = base64.urlsafe_b64decode(keys["auth"] + "==")

        # Receiver public key
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
        recv_pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), p256dh)

        # Sender ephemeral key pair
        sender_priv = ec.generate_private_key(ec.SECP256R1(), default_backend())
        sender_pub = sender_priv.public_key()
        sender_pub_bytes = sender_pub.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)

        # ECDH shared secret
        shared = sender_priv.exchange(ec.ECDH(), recv_pub)

        # HKDF for content encryption key + nonce
        salt = _os.urandom(16)

        # PRK_key
        prk_key = HKDF(hashes.SHA256(), 32, salt=auth_secret, info=b"WebPush: info\x00" + p256dh + sender_pub_bytes, backend=default_backend()).derive(shared)
        cek = HKDF(hashes.SHA256(), 16, salt=salt, info=b"Content-Encoding: aes128gcm\x00", backend=default_backend()).derive(prk_key)
        nonce = HKDF(hashes.SHA256(), 12, salt=salt, info=b"Content-Encoding: nonce\x00", backend=default_backend()).derive(prk_key)

        # Encrypt
        padded = payload + b"\x02"  # padding delimiter
        ct = AESGCM(cek).encrypt(nonce, padded, None)

        # Record size header (4096)
        rs = (4096).to_bytes(4, "big")
        # Key ID length
        idlen = len(sender_pub_bytes).to_bytes(1, "big")

        return salt + rs + idlen + sender_pub_bytes + ct
    except Exception:
        return payload


async def send_push_to_user(
    user_id: str,
    *,
    title: str,
    body: str,
    icon: str = "/hawk-icon-192.png?v=2",
    url: str = "/",
) -> int:
    """DB'deki kullanıcı subscription'larına bildirim gönder."""
    try:
        from core.pg_memory import _get_pool
        pool = await _get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT endpoint, keys FROM hawk_push_subscriptions WHERE user_id=$1 AND active=TRUE",
                user_id,
            )
        sent = 0
        for r in rows:
            sub = {"endpoint": r["endpoint"], "keys": json.loads(r["keys"] or "{}")}
            ok = await send_push(sub, title=title, body=body, icon=icon, url=url)
            if ok:
                sent += 1
        return sent
    except Exception:
        return 0
