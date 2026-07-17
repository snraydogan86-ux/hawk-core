"""
HAWK "unut / forget" niyet algılama (TR + EN) + kapsam sınıflama.

Yalnız AÇIK unutma/silme niyetinde tetiklenir. Negatifler ("unutma, yarın
toplantı var", "adımı unuttun mu?", "I forgot my password", "forget-me-not")
KESİNLİKLE silme üretmez.

Döner: {scope, keys} | None
  scope ∈ fact | keys | last | conversation | all_facts | history | all | ambiguous
"""

import re

# Alan → kanonik fact anahtarı
FIELD_MAP = {
    "ad": "user_name", "adım": "user_name", "adim": "user_name", "isim": "user_name",
    "ismim": "user_name", "name": "user_name",
    "lakab": "nickname", "nickname": "nickname",
    "şirket": "company", "sirket": "company", "firma": "company", "company": "company",
    "iş yeri": "employer", "employer": "employer",
    "proje": "user_project", "project": "user_project",
    "ürün": "product", "urun": "product", "product": "product",
    "startup": "startup", "girişim": "startup", "girisim": "startup",
    "workspace": "workspace",
    "rol": "role", "görev": "role", "gorev": "role", "role": "role",
    "meslek": "profession", "profession": "profession",
    "hedef": "goal", "amaç": "goal", "amac": "goal", "goal": "goal",
    "tercih": "preference", "preference": "preference",
    "dil": "language", "language": "language",
    "şehir": "city", "sehir": "city", "city": "city",
    "ülke": "country", "ulke": "country", "country": "country",
    "saat dilim": "timezone", "timezone": "timezone",
}
WORK_KEYS = ["company", "employer", "role", "profession", "user_project",
             "product", "startup", "workspace"]

# NEGATİF: bunlar silme DEĞİL.
_NEG = [
    r"\bunutma\b", r"\bunutmay", r"\bunuttun\s*mu", r"\bunutkan", r"\bunutmuş",
    r"\bunutmaz", r"\bunutur\s*mu", r"\bsakın\s+unut", r"\bbeni\s+unutma",
    r"\bbunu\s+unutma", r"\bunutmam", r"\bunutuyor",
    r"forget[-\s]?me[-\s]?not", r"\bi\s+forgot\b", r"\bforgot\s+my\b",
    r"\bdon'?t\s+forget\b", r"\bdo\s+not\s+forget\b", r"\bdid\s+you\s+forget\b",
    r"\bnever\s+forget\b",
]
# SİLME fiili (imperatif). \b sınırı 'unutma/unuttun' vb. ezmez.
_VERB = re.compile(
    r"\b(unut|sil|temizle|kald[ıi]r|forget|delete|clear|remove|wipe|erase)\b", re.I)
# Hafıza/öz hedefi (rastgele 'delete file' tetiklemesin diye zorunlu)
_TARGET = re.compile(
    r"\b(haf[ıi]za|haf[ıi]zan|haf[ıi]zam|memory|memories|kaydett|saved|stored|"
    r"hat[ıi]rlad|remember|ad[ıi]m|ismim|benimle|hakk[ıi]mda|beni\b|about\s+me|"
    r"my\s+\w+|bilgi|geçmiş|gecmis|history|konuşma|konusma|conversation|"
    r"şirket|proje|ürün|urun|workspace|startup|" + "|".join(map(re.escape, FIELD_MAP)) + r")",
    re.I)


def _is_negative(t: str) -> bool:
    return any(re.search(p, t, re.I) for p in _NEG)


def detect_forget_intent(message: str):
    # Türkçe İ→i küçük-harf tuzağı: "İş".lower()="i̇ş" (birleşik nokta) → temizle.
    t = (message or "").strip().lower().replace("̇", "")
    if not t:
        return None
    if _is_negative(t):
        return None
    if not _VERB.search(t) or not _TARGET.search(t):
        return None
    # soru mu? ('sildin mi', 'unuttun mu' zaten neg) — 'ne sildin' gibi sorular silme değil
    if re.search(r"\b(ne|hangi|what|which)\b.*\b(sil|unut|delete|forget|clear)", t):
        return None

    everything = re.search(r"\b(her\s*şey\w*|hersey\w*|hepsi\w*|tüm\w*|tum\w*|all|everything)\b", t)
    about_me = re.search(r"benimle\s+ilgili|hakk[ıi]mda|about\s+me|beni\b|forget\s+me\b", t)
    hist = re.search(r"geçmiş|gecmis|history|sohbet|konuşma\s+geçmiş|chat", t)
    conv = re.search(r"bu\s+konuşma|bu\s+konusma|this\s+conversation|bu\s+sohbet", t)
    last = re.search(r"\bson\b|\blast\b|en\s+son|son\s+söyled|son\s+kaydett", t)
    work = re.search(r"\biş\s+bilgi|is\s+bilgi|work\s+info|iş\s+bilgiler", t)
    mem_generic = re.search(r"haf[ıi]za|memory|memories|kaydett|saved\s+memor|stored\s+memor", t)

    # 1) tam silme (fact + history)
    if everything and about_me and (hist or "her şey" in t or "everything" in t):
        # "benimle ilgili her şeyi sil" → fact + history
        if hist or re.search(r"her\s*şey|everything", t):
            return {"scope": "all", "keys": []}
    # 2) sohbet geçmişi (facts korunur)
    if conv is None and hist and not everything and not about_me:
        return {"scope": "history", "keys": []}
    # 3) bu konuşmada kaydedilenler
    if conv:
        return {"scope": "conversation", "keys": []}
    # 4) son kaydedilen fact
    if last:
        return {"scope": "last", "keys": []}
    # 5) iş bilgileri grubu
    if work:
        return {"scope": "keys", "keys": WORK_KEYS}
    # 6) belirli alan(lar)
    found = []
    for tok, key in FIELD_MAP.items():
        if re.search(r"\b" + re.escape(tok), t):
            if key not in found:
                found.append(key)
    if found and not everything:
        return {"scope": "keys", "keys": found}
    # 7) tüm hafıza / beni unut (fact hepsi)
    if everything or about_me or mem_generic or re.search(r"forget\s+me\b|beni\s+unut", t):
        return {"scope": "all_facts", "keys": []}
    # fiil+hedef var ama kapsam net değil → netleştir
    return {"scope": "ambiguous", "keys": []}
