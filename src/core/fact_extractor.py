"""
HAWK hibrit fact extraction — pattern katmanı (deterministik).

Alanlar: user_name, nickname, company, employer, role, profession, user_project,
product, startup, workspace, goal, preference, language, country, city, timezone.

Kurallar:
 - Yalnız BİRİNCİ ŞAHIS öz-bildirimleri (projem/şirketim/ürünüm... = benim).
 - Değer bağlaç/noktalama sınırında durur.
 - NEGATİF filtreler: soru cümleleri, film/roket/nesne, "hakkında/duydum/biliyor
   musun/nedir" → fact ÜRETME.
 - Her fact: (key, value, confidence, method="pattern").
LLM structured katmanı ayrıca app tarafında bunun ÜSTÜNE merge edilir.
"""

import re

# ── Negatif sinyaller ──────────────────────────────────────────────────────
_QUESTION = ("?", "nedir", "ne dir", "mi?", "mı?", "mu?", "mü?", "misin",
             "mısın", "musun", "müsün", "biliyor mu", "what is", "who is",
             "do you know", "neydi", "ne idi")
_NOISE = ("film", "filmi", "movie", "roket", "rocket", "dizi", "kitap",
          "hakkında", "duydum", "duymuştum", "okudum", "read about",
          "heard of", "heard about", "gördüm", "izledim")


def _is_question(t: str) -> bool:
    t = (t or "").lower()
    return any(q in t for q in _QUESTION)


def _has_noise(seg: str) -> bool:
    s = (seg or "").lower()
    return any(n in s for n in _NOISE)


_EMOJI = re.compile(
    r"[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF"
    r"\U00002B00-\U00002BFF\U0000FE00-\U0000FE0F\U0000200D]+")


def _clip(value: str) -> str:
    """Değeri bağlaç/noktalama/yan-cümle sınırında kes; possessive ekleri temizle."""
    v = str(value or "").strip()
    # yan cümle / bağlaç / dolgu ("btw", "bu arada")
    v = re.split(r"\s+(?:ve|ile|and|ama|çünkü|ki|için|olarak|diye)\s+|\s+(?:btw|bu\s+arada)\b|[,;.!?\n]",
                 v, maxsplit=1, flags=re.I)[0]
    v = _EMOJI.sub("", v).strip()  # emoji/sembol ayıkla
    v = v.strip(" \t'\"“”‘’`:-–—")
    # baştaki zamir/zaman-dolgusu ayıkla: "Ben HAWK AI"→"HAWK AI", "Şu anda HAWK"→"HAWK"
    v = re.sub(r"^(?:ben\s+bir\s+|ben\s+|benim\s+|i\s+am\s+|i'?m\s+|the\s+|"
               r"şu\s*an(?:da)?\s+|şuan\s+|artık\s+|now\s+|currently\s+|right\s+now\s+|yeni\s+)",
               "", v, flags=re.I).strip()
    # "İstanbul'da" → "İstanbul" (lokatif eki ayıkla)
    v = re.sub(r"['’](?:d[ae]|t[ae]|nd[ae])$", "", v, flags=re.I).strip()
    return v


# ── Pattern tablosu: (key, regex, confidence). Grup 1 = değer. ─────────────
# Not: bazı pattern'lerde değer anahtardan ÖNCE gelir (kurucusuyum/çalışıyorum).
_P = [
    # NAME
    ("user_name", r"benim\s+ad[ıi]m\s+(.+)", 0.95),
    ("user_name", r"^\s*ad[ıi]m\s+(.+)", 0.85),
    ("user_name", r"ismim\s+(.+)", 0.9),
    ("user_name", r"\bmy\s+name\s+is\s+(.+)", 0.95),
    # NICKNAME
    ("nickname", r"bana\s+(.+?)\s+de(?:yebilirsin)?\b", 0.85),
    ("nickname", r"\bcall\s+me\s+(.+)", 0.9),
    ("nickname", r"lakab[ıi]m\s+(.+)", 0.9),
    # COMPANY
    ("company", r"şirket[ıi]m(?:in)?\s+ad[ıi]\s+(.+)", 0.95),
    ("company", r"şirket[ıi]m\s+(.+)", 0.9),
    ("company", r"firmam(?:[ıi]n)?\s+(?:ad[ıi]\s+)?(.+)", 0.9),
    ("company", r"\bmy\s+company\s+(?:is|is\s+called)\s+(.+)", 0.9),
    ("company", r"(.+?)\s+kurucusuyum", 0.85),                     # "Ben HAWK AI kurucusuyum"
    ("company", r"\bi'?m?\s+(?:the\s+)?founder\s+of\s+(.+)", 0.85),
    # EMPLOYER
    ("employer", r"çalışt[ıi]ğ[ıi]m\s+(?:şirket|firma|yer)\s+(.+)", 0.85),
    ("employer", r"\bi\s+work\s+(?:at|for)\s+(.+)", 0.85),
    # ROLE / PROFESSION
    ("role", r"mesleğim\s+(.+)", 0.85),
    ("role", r"görevim\s+(.+)", 0.8),
    ("role", r"\bi\s+work\s+as\s+(?:an?\s+)?(.+)", 0.85),
    ("profession", r"ben\s+(?:bir\s+)?(yazılımcı|mühendis|doktor|avukat|öğretmen|tasarımcı|geliştirici|developer|designer|engineer|doctor|lawyer|teacher)\b", 0.8),
    # PROJECT
    ("user_project", r"projem(?:in)?\s+ad[ıi]\s+(.+)", 0.95),
    ("user_project", r"projem\s+(.+)", 0.9),
    ("user_project", r"proje\s+ad[ıi]m\s+(.+)", 0.9),
    ("user_project", r"(.+?)\s+isimli\s+proje", 0.85),            # "Falcon-X isimli proje"
    ("user_project", r"(.+?)\s+üzerinde\s+çalış[ıi]yorum", 0.8),  # "HAWK üzerinde çalışıyorum"
    ("user_project", r"\bmy\s+project\s+is\s+(?:called\s+)?(.+)", 0.9),
    ("user_project", r"\bworking\s+on\s+(?:a\s+project\s+called\s+)?(.+)", 0.7),
    # PRODUCT
    ("product", r"ana\s+ürünüm\s+(.+)", 0.92),
    ("product", r"ürünüm(?:ün\s+ad[ıi])?\s+(.+)", 0.9),
    ("product", r"mobil\s+uygulamam\s+(.+)", 0.85),
    ("product", r"odaklandığım\s+ürün\s+(.+)", 0.85),
    ("product", r"\bmy\s+product\s+is\s+(.+)", 0.9),
    # STARTUP
    ("startup", r"startup[ıi]m\s+(.+)", 0.9),
    ("startup", r"girişimim(?:in\s+ad[ıi])?\s+(.+)", 0.9),
    ("startup", r"\bmy\s+startup\s+is\s+(.+)", 0.9),
    # WORKSPACE
    ("workspace", r"workspace\s+(?:projem|ürünüm|uygulamam)\s+(.+)", 0.88),
    # GOAL
    ("goal", r"hedefim(?:iz)?\s+(.+)", 0.85),
    ("goal", r"amac[ıi]m(?:[ıi]z)?\s+(.+)", 0.85),
    ("goal", r"\bmy\s+goal\s+is\s+(.+)", 0.85),
    # LANGUAGE / COUNTRY / CITY / TIMEZONE
    ("language", r"(?:ana\s+)?dilim\s+(.+)", 0.85),
    ("language", r"\bmy\s+language\s+is\s+(.+)", 0.85),
    ("country", r"(.+?)\s+vatandaşıyım", 0.85),
    ("country", r"(.+?)['’]?\s*(?:de|da)\s+yaşıyorum\s*$", 0.6),
    ("city", r"(.+?)['’]?\s*(?:de|da|te|ta)\s+(?:yaşıyorum|oturuyorum)", 0.7),
    ("city", r"\bi\s+live\s+in\s+(.+)", 0.75),
    ("timezone", r"saat\s+dilimim\s+(.+)", 0.85),
    ("timezone", r"\bmy\s+timezone\s+is\s+(.+)", 0.85),
    # PREFERENCE (genel)
    ("preference", r"tercihim\s+(.+)", 0.7),
    # WORKSPACE/PROJE (workspace akışı — proje adı + teknoloji yığını)
    ("project_name", r"bu\s+projenin\s+ad[ıi]\s+(.+)", 0.9),
    ("project_name", r"proje(?:nin)?\s+ad[ıi]m?[ıi]z?\s+(.+)", 0.85),
    ("project_name", r"\bthis\s+project\s+is\s+(?:called\s+)?(.+)", 0.9),
    ("tech_stack", r"(.+?)\s+kullan[ıi]yoruz", 0.8),           # "Python ve FastAPI kullanıyoruz"
    ("tech_stack", r"(?:teknoloji|stack)(?:miz)?\s+(.+)", 0.8),
    ("tech_stack", r"\bwe\s+use\s+(.+)", 0.8),
    ("repo_name", r"repo(?:muz)?\s+ad[ıi]\s+(.+)", 0.85),
]

# Kod/secret/stack-trace/.env içeriği → FACT ÜRETME (güvenlik/workspace guard)
_UNSAFE = re.compile(
    r"```|-----BEGIN|sk-[a-zA-Z0-9]{8}|api[_-]?key\s*[:=]|secret\s*[:=]|password\s*[:=]|"
    r"traceback|stack\s*trace|\bdef\s+\w+\(|\bimport\s+\w+|\bclass\s+\w+|\$\{|"
    r"[A-Z_]{3,}=[^\s]|\.env|/etc/|BEGIN\s+RSA|eyJ[A-Za-z0-9_-]{10}", re.I)


def looks_unsafe(message: str) -> bool:
    """Kod çıktısı / secret / stack-trace / env → kullanıcı fact'i sayılmaz."""
    return bool(_UNSAFE.search(str(message or "")))

_COMPILED = [(k, re.compile(p, re.I), c) for k, p, c in _P]


_COLON_KW = re.compile(
    r"(ürünüm|projem|şirketim|firmam|startup[ıi]m|girişimim|hedefim|amac[ıi]m|"
    r"ad[ıi]m|ismim|mesleğim|görevim|dilim|company|project|product|startup|name|goal)"
    r"\s*:\s*", re.I)


def pattern_extract(message: str):
    """Döner: [(key, value, confidence, 'pattern'), ...]. Soru/gürültü elenir."""
    msg = (message or "").strip()
    if not msg or _is_question(msg) or looks_unsafe(msg):
        return []   # kod/secret/env/stack-trace → fact ÜRETME
    # "ürünüm: X" → "ürünüm X" (anahtar sonrası iki-nokta normalize)
    msg = _COLON_KW.sub(lambda m: m.group(1) + " ", msg)
    out = {}
    for key, rx, conf in _COMPILED:
        m = rx.search(msg)
        if not m:
            continue
        raw = m.group(1)
        # değerin bulunduğu segmentte gürültü varsa atla (film/roket/duydum...)
        if _has_noise(raw) or _has_noise(msg[:m.start()] + " " + (m.group(1) or "")):
            continue
        if key == "tech_stack":
            # Liste değeri — "ve/and"da BÖLME (Python ve FastAPI korunur), sadece noktalama.
            val = _EMOJI.sub("", re.split(r"[,;.!?\n]", raw, maxsplit=1)[0]).strip(" \t'\"“”‘’`:-–—")
        else:
            val = _clip(raw)
        if not val or len(val) < 2 or len(val) > 60:
            continue
        # aynı key için en yüksek confidence'ı tut
        if key not in out or conf > out[key][1]:
            out[key] = (val, conf)
    return [(k, v[0], v[1], "pattern") for k, v in out.items()]
