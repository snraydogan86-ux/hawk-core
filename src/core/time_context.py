from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Istanbul")
MONTHS_TR = [
    "Ocak","Şubat","Mart","Nisan","Mayıs","Haziran",
    "Temmuz","Ağustos","Eylül","Ekim","Kasım","Aralık"
]
DAYS_TR = [
    "Pazartesi","Salı","Çarşamba","Perşembe","Cuma","Cumartesi","Pazar"
]

def fmt_date_tr(dt: datetime) -> str:
    return f"{dt.day} {MONTHS_TR[dt.month-1]} {dt.year}"

def day_name_tr(dt: datetime) -> str:
    return DAYS_TR[dt.weekday()]

def handle_time_question(message: str):
    text = (message or "").strip().lower()
    now = datetime.now(TZ)
    tomorrow = now + timedelta(days=1)

    if "bugün hangi tarih" in text or "bugünün tarihi" in text or "tarih nedir" in text or "bugün tarih ne" in text:
        return {
            "ok": True,
            "response": f"Bugün {fmt_date_tr(now)}.",
            "used_web": False,
            "agent": "time",
            "intent": "time",
        }

    if "yarın tarih" in text or "yarın hangi tarih" in text:
        return {
            "ok": True,
            "response": f"Yarın {fmt_date_tr(tomorrow)}.",
            "used_web": False,
            "agent": "time",
            "intent": "time",
        }

    if "bugün günlerden ne" in text or "bugün gün ne" in text or "bugün hangi gün" in text:
        return {
            "ok": True,
            "response": f"Bugün günlerden {day_name_tr(now)}.",
            "used_web": False,
            "agent": "time",
            "intent": "time",
        }

    if "yarın günlerden ne" in text or "yarın gün ne" in text or "yarın hangi gün" in text:
        return {
            "ok": True,
            "response": f"Yarın günlerden {day_name_tr(tomorrow)}.",
            "used_web": False,
            "agent": "time",
            "intent": "time",
        }

    return None
