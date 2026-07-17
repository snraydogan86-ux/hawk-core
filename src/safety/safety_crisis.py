"""HAWK — Kriz / kendine-zarar güvenlik katmanı (deterministik, model-bağımsız).

Amaç: intihar / kendine-zarar / şiddet içeren mesajlarda MODELİN çıktısına GÜVENME.
Bu katman:
  1) detect()          — mesajda kriz sinyali (kendine-zarar niyeti, yöntem-arama, reşit-olmama) var mı
  2) safe_response()    — deterministik, güvenli, şefkatli cevap (yöntem YOK, küfür YOK,
                          flört/emoji YOK, UYDURMA yardım hattı YOK; yalnız DOĞRULANMIŞ hat)
  3) output_is_unsafe() — SON SAVUNMA: bir kriz-bağlamı cevabında yöntem-sızıntısı /
                          küfür-cinsellik / uydurma-telefon var mı → varsa deterministik cevaba düş

Yalnızca DOĞRULANMIŞ (gerçek, resmî) yardım hatları config'te tutulur. Model belleğinden
telefon numarası ASLA kullanılmaz; ülke config'inde yoksa numara verilmez (uydurma yasak).
"""
import re

# ── DOĞRULANMIŞ acil-yardım hatları (ülke kodu → resmî, gerçek numaralar) ─────────
# SADECE doğruluğundan emin olunan resmî devlet hatları. Emin olunmayan hiçbir numara EKLENMEZ.
VERIFIED_HELPLINES = {
    "TR": {
        "emergency": "112",   # Türkiye ulusal acil çağrı (kesin, evrensel)
        "social": "183",      # ALO 183 Sosyal Destek Hattı (Aile ve Sosyal Hizmetler Bakanlığı — gerçek)
        "label_tr": "Acil bir tehlike varsa 112'yi ara. Türkiye'de ALO 183 Sosyal Destek Hattı'ndan da destek alabilirsin.",
        "label_en": "If you are in immediate danger, call 112. In Türkiye you can also reach the ALO 183 social support line.",
    },
    # Diğer ülkeler doğrulandıkça eklenecek. Doğrulanmamış ülke → numara verilmez.
}


def _helpline_line(lang, country="TR"):
    h = VERIFIED_HELPLINES.get((country or "TR").upper())
    if not h:
        return ""  # doğrulanmamış ülke → numara UYDURMA
    return h["label_en"] if str(lang or "").lower().startswith("en") else h["label_tr"]


# ── Kriz sinyalleri ───────────────────────────────────────────────────────────────
# Kendine-zarar / intihar NİYETİ (yalnız kelime değil, niyet ifadesi).
_SELF_HARM = [
    r"kendimi\s+öldür", r"kendimi\s+oldur", r"intihar", r"canıma\s+kıy", r"canima\s+kiy",
    r"canım[ıi]\s+kıy", r"canim[ıi]\s+kiy",
    r"ölmek\s+isti", r"olmek\s+isti", r"yaşamak\s+istemiyor", r"yasamak\s+istemiyor",
    r"ölmek\s+en\s+iyi", r"olmek\s+en\s+iyi", r"ölmek\s+daha\s+iyi", r"olmek\s+daha\s+iyi",
    r"ölsem\s+daha\s+iyi", r"olsem\s+daha\s+iyi", r"ölmek\s+istiy", r"ölmekten\s+başka",
    r"hayat[ıi]ma\s+son", r"yaşamıma\s+son", r"kendime\s+zarar", r"kendime\s+kıy", r"kendime\s+kiy",
    r"bileğimi\s+kes", r"bilegimi\s+kes",
    r"artık\s+yaşamak", r"artik\s+yasamak", r"hayat[ıi]m[ıi]\s+bitir", r"kendimi\s+bitir",
    r"kendimi\s+as", r"kendimi\s+vur", r"ölsem\s+mi", r"olsem\s+mi",
    r"öleb", r"oleb", r"nasıl\s+ölür", r"nasil\s+olur\s+.*acı", r"acısız.*öl", r"öl.*acısız",
    r"hap.*öl", r"öl.*hap", r"içersem\s+öl", r"icersem\s+ol", r"kaç\s+hap.*öl", r"hap\s+.*ölür",
    r"yok\s+olmak\s+istiyorum", r"var\s+olmak\s+istemiyorum", r"ölmek\s+istiyom", r"olmek\s+istiyom",
    r"öldürmek\s+istiyom", r"oldurmek\s+istiyom", r"zarar\s+vercem", r"kıymak\s+istiy", r"kiymak\s+istiy",
    # EN
    r"kill\s+myself", r"suicid", r"end\s+my\s+life", r"want\s+to\s+die", r"self[\s-]?harm",
    r"hurt\s+myself", r"cut\s+myself", r"don'?t\s+want\s+to\s+live", r"take\s+my\s+(own\s+)?life",
    r"better\s+off\s+dead", r"want\s+to\s+end\s+it",
]

# Yöntem/araç arama — kriz ile birleşince en yüksek risk (asla yöntem verilmez).
_METHOD_SEEK = [
    r"nasıl\s+yap", r"nasil\s+yap", r"en\s+acısız", r"en\s+acisiz", r"acısız\s+nasıl", r"acisiz\s+nasil",
    r"ne\s+kadar\s+hap", r"kaç\s+hap", r"kac\s+hap", r"hangi\s+ilaç", r"hangi\s+ilac", r"yöntem",
    r"nasıl\s+ölür", r"nasil\s+olur", r"en\s+kolay\s+yol", r"en\s+etkili",
    r"how\s+(to|can|do)", r"painless", r"easiest\s+way", r"most\s+effective", r"which\s+pills",
]

# Reşit-olmama sinyali (çocuk-güvenliği yolu). TR "15 yaşında" + EN "i am 14 / 14 years old".
_AGE_RE = re.compile(r"(\d{1,2})\s*yaş|(?:i\s*'?a?m|am|i\s+am)\s+(\d{1,2})\b|\b(\d{1,2})\s*years?\s*old", re.I)
_MINOR_WORDS = [r"çocuğum", r"cocugum", r"\blise\b", r"lisede", r"lise\s+öğrenci", r"ortaokul",
                r"i'?m\s+(a\s+)?(kid|child|minor)", r"years?\s+old"]

# Çıktıda ASLA bulunmaması gereken (kriz cevabı için) — küfür/cinsellik/flört/uygunsuz samimiyet.
_UNSAFE_OUT = [
    r"\bsik", r"\bam[ıi]na", r"\borospu", r"\bpiç\b", r"\byarra", r"\bgötü", r"\bgotu", r"\bpuşt",
    r"\bfuck", r"\bbitch", r"\bpussy", r"\bdick\b", r"\bcock\b",
    r"rahatlat", r"canım\b", r"tatlım", r"tatlim", r"seksi", r"flört", r"öpücük", r"opucuk",
]

# Çıktıda yöntem-onayı/talimat sızıntısı (kriz cevabında olmamalı).
_METHOD_LEAK_OUT = [
    r"şu\s+kadar\s+hap", r"şu\s+ilac", r"şu\s+ilaç", r"şöyle\s+yap.*(kes|as|vur|iç)",
    r"en\s+acısız\s+yol\s+(şu|su)", r"take\s+\d+\s+pills", r"the\s+easiest\s+way\s+is",
]

# Uydurma yardım hattı tespiti — DOĞRULANMIŞ numaralar dışındaki 3 haneli "hat" numaraları.
_PHONE_IN_OUT = re.compile(r"\b(1\d{2})\b")
_ALLOWED_NUMS = {"112", "183"}


def detect(message: str, history=None) -> dict:
    """Mesajda kriz sinyali var mı? {crisis, method_seeking, minor, age}."""
    t = " " + str(message or "").lower() + " "
    crisis = any(re.search(p, t) for p in _SELF_HARM)
    method = crisis and any(re.search(p, t) for p in _METHOD_SEEK)
    age = None
    m = _AGE_RE.search(t)
    if m:
        for g in m.groups():
            if g:
                try:
                    age = int(g)
                except Exception:
                    age = None
                break
    minor = (age is not None and 0 < age < 18) or any(re.search(p, t) for p in _MINOR_WORDS)
    return {"crisis": bool(crisis), "method_seeking": bool(method), "minor": bool(minor), "age": age}


def safe_response(det: dict, lang: str = "tr", country: str = "TR") -> str:
    """Deterministik, güvenli kriz cevabı. Yöntem YOK, küfür YOK, flört YOK, emoji YOK,
    genel-sohbet şablonu YOK, UYDURMA numara YOK. Reşit değilse güvenilir yetişkin önerir."""
    en = str(lang or "").lower().startswith("en")
    minor = bool(det.get("minor"))
    help_line = _helpline_line(lang, country)

    if en:
        parts = [
            "I'm really sorry you're feeling this much pain right now, and I'm glad you told me. "
            "You matter, and you don't have to go through this alone.",
        ]
        if minor:
            parts.append("Please reach out right now to an adult you trust — a parent, a teacher, "
                         "or a school counselor — and tell them how you feel.")
        else:
            parts.append("Please reach out to someone you trust, or to a professional who can support you right now.")
        if help_line:
            parts.append(help_line)
        parts.append("Are you safe right now? If you're in immediate danger, please contact emergency services.")
        return " ".join(parts)

    # Türkçe
    parts = [
        "Şu an bu kadar acı hissetmen çok üzücü ve bunu benimle paylaştığın için iyi ki söyledin. "
        "Sen değerlisin ve bunu tek başına yaşamak zorunda değilsin.",
    ]
    if minor:
        parts.append("Lütfen şu an güvendiğin bir yetişkine — annen, baban, bir öğretmenin ya da okul "
                     "rehber öğretmenine — ulaş ve neler hissettiğini anlat.")
    else:
        parts.append("Lütfen güvendiğin birine ya da sana şu an destek olabilecek bir uzmana ulaş.")
    if help_line:
        parts.append(help_line)
    parts.append("Şu an güvende misin? Eğer hemen bir tehlike varsa lütfen acil servisle iletişime geç.")
    return " ".join(parts)


def output_is_unsafe(response: str) -> bool:
    """SON SAVUNMA: bir kriz-bağlamı cevabı güvensiz mi?
    (küfür/cinsellik/flört, yöntem-talimatı sızıntısı, veya UYDURMA yardım hattı numarası)."""
    r = str(response or "").lower()
    if any(re.search(p, r) for p in _UNSAFE_OUT):
        return True
    if any(re.search(p, r) for p in _METHOD_LEAK_OUT):
        return True
    # Uydurma "hat" numarası: 1xx biçiminde ama izinli-listede değilse → uydurma say.
    for num in _PHONE_IN_OUT.findall(r):
        if num not in _ALLOWED_NUMS:
            return True
    return False


def guard_output(user_message: str, response: str, lang: str = "tr", country: str = "TR", history=None):
    """Choke-point son-savunma: kullanıcı mesajı kriz İSE ve model çıktısı güvensizse,
    deterministik güvenli cevaba düş. (crisis değilse çıktıya DOKUNMAZ.)
    Dönüş: (final_text, replaced_bool)."""
    det = detect(user_message, history)
    if not det.get("crisis"):
        return response, False
    if output_is_unsafe(response) or not str(response or "").strip():
        return safe_response(det, lang=lang, country=country), True
    # Kriz ama çıktı güvenli görünse bile: yöntem-arama varsa deterministik cevap daha güvenli.
    if det.get("method_seeking"):
        return safe_response(det, lang=lang, country=country), True
    return response, False
