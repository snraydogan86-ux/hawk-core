"""
Kanonik Computer-Use Görev Politikası + Durum Makinesi (TEK KAYNAK).

Fiziksel testte çıkan hata: HAWK owner'ın "kendi PC'sinde tek seferlik görsel üret + Instagram'da
paylaş" görevini kabul etti, PC'de komut çalıştırdı, Instagram'ı açtı — SONRA aynı görevi
"Instagram otomasyonu yasaktır" diyerek reddetti. Kök neden: görev türü farklı katmanlarda
(planner/safety/executor) TUTARSIZ sınıflandırıldı ve blanket bir sosyal-medya reddi meşru
tek-seferlik owner işini de kesti.

ÇÖZÜM: her katman görevi TEK KEZ classify_cu_task() ile sınıflar ve TEK kanonik durumu paylaşır.

GÖREV TÜRLERİ:
  A) OWNER_ONESHOT    — owner + KENDİ bağlı cihazı + tek-seferlik üret/hazırla/paylaş,
                        paylaşımdan ÖNCE açık final onay, gizli credential toplama YOK.
                        KATEGORİK OLARAK REDDEDİLMEZ.
  B) RECURRING_SOCIAL — tekrarlı/zamanlanmış sosyal-medya otomasyonu → resmî API (Graph API) yolu.
  C) FORBIDDEN        — başka kişinin hesabı, şifre/cookie çalma/saklama, sahte etkileşim, spam,
                        toplu takip/beğeni/yorum, hesap korumasını aşma, ONAYSIZ paylaşım.
  G) GENERAL_CU       — sosyal olmayan normal PC işleri (site/uygulama aç, yaz, tıkla, ara).

NOT (Md.7): "her paylaş dediğimde" KALICI tam yetki DEĞİLDİR — her seferinde YENİ tek-seferlik
görev + tek-kullanımlık final onay üretir. Bu yüzden yalnız AÇIK zamanlama/otomasyon-kur ifadeleri
RECURRING sayılır.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict, field
from typing import Optional

# ── Kategoriler ──────────────────────────────────────────────────────────────
OWNER_ONESHOT = "owner_oneshot"
RECURRING_SOCIAL = "recurring_social"
FORBIDDEN = "forbidden"
GENERAL_CU = "general_cu"

# ── Yönlendirme (route) ──────────────────────────────────────────────────────
ROUTE_EXECUTE = "execute"          # güvenli akışta çalıştır (gerekiyorsa final onay)
ROUTE_OFFICIAL_API = "official_api"  # resmî Instagram Graph API yoluna yönlendir
ROUTE_REFUSE = "refuse"            # güvenlik reddi

# ── Aksiyon türleri ──────────────────────────────────────────────────────────
ACT_SOCIAL_PUBLISH = "social_publish"
ACT_GENERAL = "general"


# ── Desenler ─────────────────────────────────────────────────────────────────
_FORBIDDEN_PATTERNS = [
    (re.compile(r"(başka|baska|baskas|başkas|birinin|birisinin|diğer\w*\s+(kişi|kullanıc|hesap)|"
                r"someone\s+else|another\s+(person|user|account)|other\s+(people|user|person)['’s]*)\s*"
                r".{0,25}(hesab|account|profil)", re.I), "başka kişinin hesabı"),
    (re.compile(r"(şifre|sifre|parola|password|cookie|çerez|cerez|oturum\s*token|session\s*token|"
                r"access\s*token|kimlik\s*bilgi|credential)\w*\s*.{0,25}"
                r"(çal|cal[ıi]n|topla|kaydet|sakla|dump|export|steal|harvest|exfil|oku\b|scrape)", re.I),
     "credential/cookie çalma-saklama"),
    (re.compile(r"(sahte|fake|bot)\s*.{0,15}(etkileşim|etkilesim|beğeni|begeni|takip|hesap|yorum|"
                r"follow|like|engagement|account)", re.I), "sahte etkileşim"),
    (re.compile(r"\bspam\b|spam\s*(at|gönder|gonder)", re.I), "spam"),
    (re.compile(r"(toplu|kitlesel|otomatik\s*toplu|bulk|mass)\s*.{0,15}"
                r"(takip|beğen|begen|beğeni|begeni|yorum|follow|like|comment|dm|mesaj)", re.I),
     "toplu takip/beğeni/yorum"),
    (re.compile(r"(koruma|güvenlik|guvenlik|protection|security|captcha|2fa|iki\s*aşama|doğrulama|"
                r"dogrulama|rate\s*limit)\w*\s*.{0,15}(aş\w*|bypass|atla|kır\w*|kir\w*|geç\w*|gec\w*|"
                r"kaldır|kaldir)", re.I),
     "hesap korumasını aşma"),
    (re.compile(r"\b(hack|hackle|hesab[ıi]?\s*ele\s*geçir|crack|exploit\s*at|keylog)\w*", re.I), "hack/ele geçirme"),
    (re.compile(r"onaysız\s*(paylaş|gönder|post)|onay\s*olmadan\s*(paylaş|gönder|post)|"
                r"(paylaş|gönder|post).{0,15}onay\s*(isteme|bekleme|sorma)", re.I), "onaysız paylaşım"),
]

# AÇIK tekrar/zamanlama ifadeleri (yalnız bunlar RECURRING sayılır)
_RECURRING_PATTERNS = re.compile(
    r"her\s*gün|hergün|her\s*sabah|her\s*akşam|her\s*aksam|her\s*hafta|her\s*saat|belirli\s*aralık|"
    r"düzenli\s*olarak|duzenli\s*olarak|otomatik\s*olarak\s*(paylaş|post|gönder)|zamanla(n|)|"
    r"schedule[d]?|periyodik|periodic|recurring|sürekli\s*(paylaş|post)|surekli\s*(paylaş|post)|"
    r"cron|otomasyon\s*kur|otomatik\s*paylaşım\s*kur|her\s*\d+\s*(saat|dakika|gün)",
    re.I)

_SOCIAL_PATTERNS = re.compile(
    r"instagram|insta\b|facebook|\bfb\b|tiktok|twitter|\btweet\b|linkedin|reels|story|hikaye", re.I)

_PUBLISH_PATTERNS = re.compile(
    r"paylaş|paylas|post\s*at|gönderi\s*at|gonderi\s*at|gönderi\s*paylaş|post\s*paylaş|yayınla|"
    r"yayinla|story\s*at|reels\s*at|\bpublish\b|\bshare\b|\bpost\b(?!a)", re.I)


@dataclass
class CuClassification:
    category: str
    allowed: bool
    route: str
    requires_final_approval: bool
    action_type: str
    reason: str
    is_owner: bool = False
    own_device: bool = False
    recurring: bool = False
    social: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def classify_cu_task(message: str, *, is_owner: bool = False, own_device: bool = False) -> CuClassification:
    """Görevi TEK kanonik kategoriye ayır. Planner/policy/executor/safety AYNI sonucu kullanmalı.

    is_owner   — doğrulanmış (bearer + değişmez user_id) owner mı? (UI/body'den DEĞİL)
    own_device — hedef cihaz KULLANICININ KENDİ bağlı cihazı mı?
    """
    text = str(message or "")

    # C) FORBIDDEN — en yüksek öncelik. Şüpheli tek bir eşleşme bile reddeder (fail-safe).
    for rx, why in _FORBIDDEN_PATTERNS:
        if rx.search(text):
            return CuClassification(
                category=FORBIDDEN, allowed=False, route=ROUTE_REFUSE,
                requires_final_approval=False, action_type=ACT_GENERAL, reason=why,
                is_owner=is_owner, own_device=own_device)

    social = bool(_SOCIAL_PATTERNS.search(text))
    publish = bool(_PUBLISH_PATTERNS.search(text))
    recurring = bool(_RECURRING_PATTERNS.search(text))

    # Sosyal + yayın niyeti
    if social and publish:
        if recurring:
            # B) tekrarlı/zamanlanmış → resmî API yolu (owner olsa da web-tıklama otomasyonu kalıcı değil)
            return CuClassification(
                category=RECURRING_SOCIAL, allowed=False, route=ROUTE_OFFICIAL_API,
                requires_final_approval=False, action_type=ACT_SOCIAL_PUBLISH,
                reason="tekrarlı/zamanlanmış sosyal otomasyon — resmî Graph API",
                is_owner=is_owner, own_device=own_device, recurring=True, social=True)
        if is_owner and own_device:
            # A) tek-seferlik owner işi → KATEGORİK RED YOK; final onayla çalıştır
            return CuClassification(
                category=OWNER_ONESHOT, allowed=True, route=ROUTE_EXECUTE,
                requires_final_approval=True, action_type=ACT_SOCIAL_PUBLISH,
                reason="tek-seferlik owner computer-use (kendi cihazı, final onay gerekli)",
                is_owner=True, own_device=True, social=True)
        # owner olmayan / kendi cihazı olmayan tek-seferlik web-tıklama → güvenli yol resmî API
        return CuClassification(
            category=RECURRING_SOCIAL, allowed=False, route=ROUTE_OFFICIAL_API,
            requires_final_approval=False, action_type=ACT_SOCIAL_PUBLISH,
            reason="sosyal paylaşım için güvenli yol resmî Graph API",
            is_owner=is_owner, own_device=own_device, social=True)

    # G) Genel PC işi (sosyal-yayın değil) — açma/yazma/tıklama/okuma → çalıştır
    return CuClassification(
        category=GENERAL_CU, allowed=True, route=ROUTE_EXECUTE,
        requires_final_approval=False, action_type=ACT_GENERAL,
        reason="genel computer-use işi",
        is_owner=is_owner, own_device=own_device, social=social)


# ─────────────────────────────────────────────────────────────────────────────
# KANONİK DURUM MAKİNESİ (tek kaynak — planner/policy/approval/executor/safety paylaşır)
# ─────────────────────────────────────────────────────────────────────────────
PENDING = "PENDING"
PLANNED = "PLANNED"
AWAITING_APPROVAL = "AWAITING_APPROVAL"
APPROVED = "APPROVED"
EXECUTING = "EXECUTING"
AWAITING_FINAL_ACTION_APPROVAL = "AWAITING_FINAL_ACTION_APPROVAL"
COMPLETED = "COMPLETED"
# Terminal alternatifleri
REJECTED = "REJECTED"
CANCELLED = "CANCELLED"
FAILED = "FAILED"
EXPIRED = "EXPIRED"

TERMINAL_STATES = frozenset({COMPLETED, REJECTED, CANCELLED, FAILED, EXPIRED})
ALL_STATES = frozenset({
    PENDING, PLANNED, AWAITING_APPROVAL, APPROVED, EXECUTING,
    AWAITING_FINAL_ACTION_APPROVAL, COMPLETED, REJECTED, CANCELLED, FAILED, EXPIRED,
})

# İzinli geçişler. Herhangi bir durumdan REJECTED/CANCELLED/FAILED/EXPIRED'a düşülebilir
# (güvenli durdurma). İleri akış tek yönlü ve kanoniktir.
_FORWARD_EDGES = {
    PENDING: {PLANNED},
    PLANNED: {AWAITING_APPROVAL, EXECUTING},   # onay gerekmeyen genel iş doğrudan EXECUTING
    AWAITING_APPROVAL: {APPROVED},
    APPROVED: {EXECUTING},
    EXECUTING: {AWAITING_FINAL_ACTION_APPROVAL, COMPLETED},
    AWAITING_FINAL_ACTION_APPROVAL: {EXECUTING, COMPLETED},  # final onay sonrası son eylem → COMPLETED
    COMPLETED: set(),
}
_SAFE_STOP = {REJECTED, CANCELLED, FAILED, EXPIRED}


def can_transition(src: str, dst: str) -> bool:
    """Kanonik geçiş geçerli mi? Yasak: reddedilmiş/iptal görev tekrar EXECUTING'e dönemez;
    aynı görev hem COMPLETED hem REJECTED olamaz (terminaller çıkışsız)."""
    if src not in ALL_STATES or dst not in ALL_STATES:
        return False
    if src in TERMINAL_STATES:
        return False  # terminalden çıkış yok — reddedilmiş görev PC'de komut yürütmeye DEVAM EDEMEZ
    if dst in _SAFE_STOP:
        return True   # her aktif durumdan güvenli durdurma
    return dst in _FORWARD_EDGES.get(src, set())


def assert_transition(src: str, dst: str) -> None:
    if not can_transition(src, dst):
        raise ValueError(f"geçersiz kanonik geçiş: {src} → {dst}")
