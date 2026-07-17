"""
PII redaksiyonu — TC kimlik, telefon, kredi kartı için maskeleme.

Amaç: kullanıcı/model PII'sinin hafızaya/DB'ye kalıcılaşmasını ve cevaba
yansımasını önlemek (KVKK/gizlilik). YÜKSEK HASSASİYET: her aday sayı
matematiksel olarak DOĞRULANIR (TC checksum, kart Luhn, telefon açık önek),
böylece normal metindeki rakamlar (fiyat, yıl, miktar) maskelenmez.
Saf regex + doğrulama — deterministik test edilir.
"""
from __future__ import annotations

import re

TC_MASK = "[TCKN_GIZLI]"
PHONE_MASK = "[TELEFON_GIZLI]"
CARD_MASK = "[KART_GIZLI]"
IBAN_MASK = "[IBAN_GIZLI]"
EMAIL_MASK = "[EMAIL_GIZLI]"

# --- aday desenleri (doğrulama fonksiyonu geçerliyse maskelenir) ---
# Kredi kartı: 13-19 hane, gruplar boşluk/tire ile ayrılabilir.
_CARD_RX = re.compile(r"(?<![\dA-Za-z])(?:\d[ -]?){13,19}(?![\dA-Za-z])")
# TC: tam 11 hane, bitişik (araya boşluk/tire girmez).
_TC_RX = re.compile(r"(?<!\d)\d{11}(?!\d)")
# Telefon: açık önek (+90 / 0090 / 0) + 10 hane; araya boşluk/tire/parantez.
_PHONE_RX = re.compile(
    r"(?<![\d+])(?:\+90|0090|0)[\s.\-]?\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{2}[\s.\-]?\d{2}(?!\d)"
)
# IBAN: 2 harf ülke + 2 kontrol + gövde. mod-97 ile DOĞRULANIR.
# İki dal: (a) kompakt bitişik 11-30 alnum, (b) boşlukla 4'lü gruplar — böylece
# opsiyonel-boşluk sonraki kelimeyi yutmaz (ör. '...26 gonder' → 'gonder' dahil edilmez).
_IBAN_RX = re.compile(
    r"(?<![A-Za-z0-9])[A-Za-z]{2}\d{2}"
    r"(?:[A-Za-z0-9]{11,30}|(?:[ ][A-Za-z0-9]{2,4}){2,8})(?![A-Za-z0-9])"
)
# Email: standart RFC-benzeri; yüksek hassasiyet.
_EMAIL_RX = re.compile(r"(?<![A-Za-z0-9._%+\-])[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def _luhn_ok(num: str) -> bool:
    total, alt = 0, False
    for ch in reversed(num):
        d = ord(ch) - 48
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


def _tc_ok(num: str) -> bool:
    if len(num) != 11 or num[0] == "0":
        return False
    d = [ord(c) - 48 for c in num]
    odd = d[0] + d[2] + d[4] + d[6] + d[8]      # 1,3,5,7,9. haneler
    even = d[1] + d[3] + d[5] + d[7]            # 2,4,6,8. haneler
    if (odd * 7 - even) % 10 != d[9]:
        return False
    if sum(d[:10]) % 10 != d[10]:
        return False
    return True


def _iban_ok(s: str) -> bool:
    """IBAN mod-97 doğrulaması (ISO 13616). Rastgele alfanümerik dizi maskelenmez."""
    s = re.sub(r"\s", "", s).upper()
    if not (15 <= len(s) <= 34) or not s[:2].isalpha() or not s[2:4].isdigit():
        return False
    rearr = s[4:] + s[:4]
    num = "".join(str(ord(c) - 55) if c.isalpha() else c for c in rearr)
    try:
        return int(num) % 97 == 1
    except ValueError:
        return False


def redact_pii(text: str) -> tuple[str, int]:
    """(maskelenmiş_metin, bulunan_pii_sayısı). PII yoksa metin aynen döner.

    Sayaç YALNIZ gerçek maskede artar: doğrulamayı geçemeyen aday (geçersiz TC
    checksum / Luhn'suz kart) ham bırakılır ve sayılmaz.
    """
    if not text or not isinstance(text, str):
        return text, 0
    count = [0]

    def sub_card(m: re.Match) -> str:
        raw = m.group(0)
        num = _digits(raw)
        if 13 <= len(num) <= 19 and _luhn_ok(num):
            count[0] += 1
            return CARD_MASK
        return raw

    def sub_phone(m: re.Match) -> str:
        count[0] += 1
        return PHONE_MASK

    def sub_tc(m: re.Match) -> str:
        raw = m.group(0)
        if _tc_ok(raw):
            count[0] += 1
            return TC_MASK
        return raw

    def sub_iban(m: re.Match) -> str:
        raw = m.group(0)
        if _iban_ok(raw):
            count[0] += 1
            return IBAN_MASK
        return raw

    def sub_email(m: re.Match) -> str:
        count[0] += 1
        return EMAIL_MASK

    # Sıra: IBAN (mod-97) → kart (Luhn) → telefon → TC → email.
    # IBAN önce: harf+rakam dizisi, kartın rakam-grubuyla çakışmasın.
    out = _IBAN_RX.sub(sub_iban, text)
    out = _CARD_RX.sub(sub_card, out)
    out = _PHONE_RX.sub(sub_phone, out)
    out = _TC_RX.sub(sub_tc, out)
    out = _EMAIL_RX.sub(sub_email, out)
    return out, count[0]


def has_pii(text: str) -> bool:
    return redact_pii(text)[1] > 0
