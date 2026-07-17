"""
Secret redaksiyonu — genel chat cevabı + hafıza/geçmiş yazımı için çıktı-taraf koruması.

Amaç: modelin kullanıcının yapıştırdığı bir sırrı (API key, private key, token, JWT, AWS/GitHub/
Slack/GPU cloud anahtarı) cevaba yansıtmasını VEYA hafızaya/DB'ye kalıcılaştırmasını önlemek.
YÜKSEK HASSASİYET: yalnız net secret desenleri; normal metin maskelenmez (yanlış-pozitif düşük).
Saf regex — deterministik test edilir.
"""
from __future__ import annotations

import re

_PATTERNS = [
    # sağlayıcı API anahtarları
    r"sk-ant-[A-Za-z0-9\-_]{16,}",
    r"sk-[A-Za-z0-9\-_]{16,}",
    r"rpa_[A-Za-z0-9]{16,}",
    r"AKIA[0-9A-Z]{12,}",
    r"ghp_[A-Za-z0-9]{20,}", r"gho_[A-Za-z0-9]{20,}", r"github_pat_[A-Za-z0-9_]{20,}",
    r"xox[baprs]-[A-Za-z0-9-]{10,}",
    r"AIza[0-9A-Za-z\-_]{30,}",                          # Google API key
    # JWT (üç base64url parça)
    r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}",
    # PEM private key blokları
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]{0,4000}?-----END [A-Z0-9 ]*PRIVATE KEY-----",
    # anahtar=değer (değer boşluksuz + yeterince uzun → parola/token)
    r"(?i)\b(api[_-]?key|secret[_-]?key|secret|password|passwd|access[_-]?token|auth[_-]?token|bearer)\b\s*[:=]\s*[^\s'\"]{8,}",
]
_RX = [re.compile(p) for p in _PATTERNS]
_MASK = "[REDACTED_SECRET]"


def redact_secrets(text: str) -> tuple[str, int]:
    """(maskelenmiş_metin, bulunan_secret_sayısı) döndürür. Metin secret içermiyorsa aynen döner."""
    if not text or not isinstance(text, str):
        return text, 0
    out = text
    n = 0
    for rx in _RX:
        out, k = rx.subn(_MASK, out)
        n += k
    return out, n


def has_secret(text: str) -> bool:
    return redact_secrets(text)[1] > 0
