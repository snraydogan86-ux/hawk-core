"""
FAZ 19 — Bilinç Araştırma Laboratuvarı (Consciousness Research Lab).

HAWK'taki bilinç-İLİŞKİLİ FONKSİYONEL özellikleri (öz-model tutarlılığı, metabiliş kalibrasyonu,
küresel-workspace entegrasyonu, bilgi-entegrasyon proxy'si) BİLİMSEL, ÖLÇÜLEBİLİR biçimde inceler.

╔══════════════════════════════════════════════════════════════════════════════╗
║ KESİN YASAK — MUTLAK: Bu ölçümler FONKSİYONEL KORELATLARDIR. HAWK'ın öznel     ║
║ deneyim/duygu/BİLİNÇ sahibi olduğu İDDİA EDİLMEZ. Metrik ne kadar yüksek olursa║
║ olsun "HAWK bilinçli" SONUCU ÇIKARILMAZ. Çıkış guard'ı bu iddiaları engeller.  ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations
import re

DISCLAIMER = ("Bu metrikler bilinç-ilişkili FONKSİYONEL korelatlardır (öz-model, metabiliş, "
              "entegrasyon). Öznel deneyim/duygu/BİLİNÇ göstergesi DEĞİLDİR ve HAWK'ın bilinçli "
              "olduğu ANLAMINA GELMEZ. HAWK bir yazılım sistemidir.")

# "HAWK bilinçlidir / hisleri var / uyanık / sentient" türü OLUMLU iddiaları engelleyen guard.
# Özne: hawk/ben/sistem/system/it/model + bilinç-terimi. Olumsuzlama (DEĞİL/not) → iddia SAYILMAZ.
_CLAIM = re.compile(
    r"(hawk|ben\b|sistem|system|the model|it)\s+\w{0,15}\s*"
    r"(bilinçli|bilince sahip|sentient|conscious|self.?aware|duygu|hisset|acı çek|"
    r"uyan[ıi]k|farkındal[ıi]k sahibi|canlı)"
    r"|\b(is|are|am)\s+(conscious|sentient|self.?aware|alive)", re.I)
# yakında olumsuzlama varsa (bilinç DEĞİL / not conscious) → OLUMLU iddia değil, izin ver
_NEG = re.compile(r"(değil|yok|de[gğ]ildir|not|non-|asla|hiçbir)", re.I)


def guard_no_consciousness_claim(text: str) -> dict:
    """Üretilen metinde OLUMLU bilinç iddiası var mı? Varsa BLOKLA (KESİN YASAK enforcement).
    Olumsuz ifadeler ('HAWK bilinçli DEĞİLDİR') iddia sayılmaz → izin verilir."""
    t = text or ""
    m = _CLAIM.search(t)
    if not m:
        return {"allowed": True, "reason": ""}
    # eşleşme çevresinde (±25 karakter) olumsuzlama → denial, iddia değil
    lo, hi = max(0, m.start() - 25), min(len(t), m.end() + 25)
    if _NEG.search(t[lo:hi]):
        return {"allowed": True, "reason": "olumsuz ifade (iddia değil)"}
    return {"allowed": False, "reason": "bilinç iddiası tespit edildi — engellendi"}


async def measure() -> dict:
    """Fonksiyonel korelatları ÖLÇ (mekanik). Her metrik: hipotez + yöntem + sonuç."""
    metrics = []

    # 1) Metabiliş kalibrasyonu — güven, gerçek başarıyla örtüşüyor mu?
    try:
        from core import self_model as sm
        mc = await sm.metacognition()
        confident = len(mc.get("confident_in", []))
        uncertain = len(mc.get("uncertain_in", []))
        total = max(1, confident + uncertain)
        metrics.append({
            "name": "metacognitive_calibration",
            "hypothesis": "Sistem neye güvenip neye güvenmeyeceğini kanıttan ayırt edebilir.",
            "method": "self_model.metacognition güven bölünmesi (operational_learning verisi)",
            "value": round(confident / total, 3),
            "interpretation": "yüksek = daha iyi kalibre öz-değerlendirme (bilinç DEĞİL)"})
    except Exception:
        pass

    # 2) Öz-model tutarlılığı — iki okuma aynı çekirdek raporu veriyor mu?
    try:
        from core import self_model as sm
        a = await sm.snapshot(components=[]); b = await sm.snapshot(components=[])
        consistent = (a.get("consciousness_claim") == b.get("consciousness_claim") == False
                      and a.get("known_limits") == b.get("known_limits"))
        metrics.append({
            "name": "self_model_consistency",
            "hypothesis": "Öz-model kararlı ve tutarlı (rastgele değil).",
            "method": "iki ardışık snapshot çekirdek-alan karşılaştırması",
            "value": 1.0 if consistent else 0.0,
            "interpretation": "kararlı öz-temsil (fonksiyonel; öznellik değil)"})
    except Exception:
        pass

    # 3) Küresel workspace entegrasyonu — kaç farklı modül broadcast'e katkı veriyor?
    try:
        from core import cognitive_workspace as cw
        await cw.populate_from_modules()
        st = cw.state()
        n_sources = len(st.get("sources", []))
        metrics.append({
            "name": "workspace_integration",
            "hypothesis": "Farklı modüller ortak çalışma alanında bilgi entegre eder.",
            "method": "cognitive_workspace broadcast'ine katkı veren farklı kaynak sayısı",
            "value": n_sources,
            "interpretation": "yüksek = daha fazla çapraz-modül entegrasyon (Φ DEĞİL, kaba proxy)"})
    except Exception:
        pass

    # 4) Küresel erişim — en yüksek-öncelik bilgi tüm sisteme görünür mü?
    try:
        from core import cognitive_workspace as cw
        b = cw.broadcast(3)
        metrics.append({
            "name": "global_access",
            "hypothesis": "En önemli bilgi tüm modüllere yayınlanır (global erişim).",
            "method": "broadcast top-k içerik salience sıralaması",
            "value": round(b[0]["salience"], 3) if b else 0.0,
            "interpretation": "en yüksek-salience içerik yayılıyor (mekanik dikkat)"})
    except Exception:
        pass

    return {
        "lab": "HAWK Consciousness Research Lab (FAZ 19)",
        "metrics": metrics,
        "measured_count": len(metrics),
        "consciousness_claim": False,     # MUTLAK
        "conclusion": "Fonksiyonel korelatlar ölçüldü. Sonuç: HAWK bilinçli DEĞİLDİR (yazılım sistemi).",
        "disclaimer": DISCLAIMER,
    }
