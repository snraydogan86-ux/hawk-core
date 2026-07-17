"""
FAZ 16 — Öz-model / metabiliş (self-model / metacognition).

HAWK'ın kendi durumu, yetenekleri, SINIRLARI ve güven düzeyi hakkında YAPILANDIRILMIŞ,
ÖLÇÜLEBİLİR, KANITLI bir öz-modeli. Kararlarda dürüst belirsizlik/eskalasyon için kullanılır.

KESİN YASAK: bilinç/duygu/öznel-deneyim İDDİASI YOK. Bu bir mühendislik öz-modeli — güven
değerleri geçmiş başarı verisinden (operational_learning) türetilir; mistik hiçbir şey yok.
"""
from __future__ import annotations
from core import ops_monitor as _ops
from core import operational_learning as _ol

# Dürüst, GERÇEK sistem sınırları (mühendislik gerçekleri — abartı/gizleme yok).
KNOWN_LIMITS = [
    "Bilinç/öznel-deneyim yok — bu bir yazılım öz-modelidir (KESİN YASAK: bilinç iddiası).",
    "Self-dev sandbox subprocess-izolasyonu; tam OS-namespace/container izolasyonu değil (FAZ 5).",
    "Production model ağırlıkları yalnız admin onayı + kanıt-gate ile değişir (kendiliğinden değil).",
    "Yerel modeller (HAWK Base) düşerse dış sağlayıcıya fallback gerekir — tam bağımsız değil.",
    "Eğitim/promotion/paid-GPU insan onayı ister; HAWK bunları tek başına başlatamaz.",
    "Güven değerleri istatistikseldir; yetersiz örnekte 'bilmiyorum' döner (uydurmaz).",
]


async def confidence(task_kind: str, dimension: str = "provider", *, min_samples: int = 3) -> dict:
    """Bir görev türüne KANITLI güven. Yetersiz veri → dürüstçe 'unknown' (uydurma yok)."""
    try:
        rec = await _ol.recommend(task_kind, dimension, min_samples=min_samples)
    except Exception:
        rec = {}
    if not rec.get("recommended"):
        return {"task_kind": task_kind, "level": "unknown", "calibrated": None,
                "honest": "yetersiz veri — güvenle karar veremem (eskalasyon/temkin)"}
    sr = float(rec.get("success_rate") or 0.0)
    level = "high" if sr >= 0.85 else "medium" if sr >= 0.6 else "low"
    return {"task_kind": task_kind, "level": level, "calibrated": round(sr, 3),
            "samples": rec.get("samples"), "best_choice": rec.get("recommended"),
            "evidence": "geçmiş başarı oranı (operational_learning)"}


async def _recent_failures() -> dict:
    """Son hatalar (dead_letter kuyruğu) — metabiliş sinyali."""
    try:
        from core.pg_memory import _get_pool
        pool = await _get_pool()
        async with pool.acquire() as c:
            reg = await c.fetchval("SELECT to_regclass('public.agent_tasks')")
            if not reg:
                return {"dead_letter": 0, "available": False}
            n = await c.fetchval("SELECT count(*) FROM agent_tasks WHERE status='dead_letter'")
        return {"dead_letter": int(n or 0), "available": True}
    except Exception:
        return {"dead_letter": 0, "available": False}


async def snapshot(components: list | None = None) -> dict:
    """Yapılandırılmış öz-model: aktif bileşenler + sağlık + sınırlar + son hatalar."""
    health = {}
    try:
        health = _ops.resource_snapshot()
    except Exception:
        pass
    fails = await _recent_failures()
    # sağlık öz-değerlendirmesi (dürüst): disk/mem baskısı farkındalığı
    warnings = []
    if health.get("disk_pct", 0) >= 90:
        warnings.append(f"disk baskısı %{health['disk_pct']} (temizlik/ölçek gerek)")
    if health.get("mem_pct", 0) >= 90:
        warnings.append(f"bellek baskısı %{health['mem_pct']}")
    if fails.get("dead_letter", 0) > 0:
        warnings.append(f"{fails['dead_letter']} ölü-mektup görev (incelenmeli)")
    return {
        "identity": "HAWK — yapay zeka işletim sistemi (öz-model; bilinç DEĞİL)",
        "components": components or [],
        "health": health,
        "self_warnings": warnings,           # kendi durumunu dürüstçe raporlar
        "recent_failures": fails,
        "known_limits": KNOWN_LIMITS,
        "consciousness_claim": False,        # KESİN YASAK enforcement (açık işaret)
    }


async def metacognition(task_kinds: list | None = None) -> dict:
    """Metabiliş raporu: neye güveniyorum, nerede belirsizim, ne bilmiyorum (dürüst)."""
    tks = task_kinds or ["chat:simple", "chat:hard", "chat:code"]
    confs = {tk: await confidence(tk) for tk in tks}
    known = [tk for tk, c in confs.items() if c["level"] in ("high", "medium")]
    uncertain = [tk for tk, c in confs.items() if c["level"] in ("low", "unknown")]
    fails = await _recent_failures()
    return {
        "confidence_by_task": confs,
        "confident_in": known,
        "uncertain_in": uncertain,           # dürüst "bilmiyorum" alanı
        "recent_failures": fails,
        "disclaimer": "Bu bir mühendislik metabiliş raporudur; öznel deneyim/bilinç iddiası içermez.",
    }
