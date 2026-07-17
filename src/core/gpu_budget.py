"""
Birleşik GPU/ücretli-kaynak bütçe kapısı — TÜM SAĞLAYICILARIN günlük toplamı $3'ü GEÇEMEZ.

Local Worker sistemi için tek doğruluk kaynağı: her ücretli pod açılışı (GPU cloud eğitim, serving,
benchmark, ...) ÖNCE can_spend() ile kontrol edilir. $3'te GÜVENLİ DURDURMA + checkpoint. Ayrıca
AYNI ANDA TEK EĞİTİM (training lock). Hata/timeout/bağlantı-kaybında ücretli GPU'lar kapatılır.

DÜRÜST tahmin: gerçek GPU cloud faturası ledger'a est_usd_hr × süre ile eklenir; kesin fiyat pod
tipiyle değişir (fiyat ölçülmeden pod açma yasağı: launcher gerçek GPU fiyatını raporlar).
"""
from __future__ import annotations
import json
import os
import time

_DIR = os.getenv("HAWK_MEMORY_DIR", "/data/hawk_memory")
LEDGER = os.path.join(_DIR, "gpu_budget_ledger.json")
LOCK = os.path.join(_DIR, "training_lock.json")
DAILY_CAP_USD = float(os.getenv("HAWK_GPU_DAILY_CAP_USD", "3"))   # TÜM sağlayıcılar toplamı
TRAIN_LOCK_TTL_S = int(os.getenv("HAWK_TRAIN_LOCK_TTL_S", "3600"))


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def _load(p, d):
    try:
        with open(p) as f:
            return json.load(f)
    except Exception:
        return d


def _save(p, obj):
    tmp = p + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f)
    os.replace(tmp, p)


def spent_today() -> float:
    return float(_load(LEDGER, {}).get(_today(), 0.0))


def remaining() -> float:
    return round(max(0.0, DAILY_CAP_USD - spent_today()), 4)


def can_spend(est_usd: float, *, provider: str = "gpu_cloud") -> dict:
    """Bu harcamaya (tahmini) izin var mı? Kill-switch + günlük tavan + kalan bütçe kontrolü."""
    try:
        import core.cost_guard as cg
        if cg.is_killed() or cg.is_killed("gpu_cloud") or cg.is_killed("training"):
            return {"ok": False, "reason": "kill-switch aktif"}
    except Exception:
        pass
    sp = spent_today()
    if sp >= DAILY_CAP_USD:
        return {"ok": False, "reason": f"günlük tavan DOLDU: ${sp:.2f}/${DAILY_CAP_USD} (güvenli durdurma)"}
    if sp + max(0.0, est_usd) > DAILY_CAP_USD:
        return {"ok": False, "reason": f"tahmini harcama tavanı aşar: ${sp:.2f}+${est_usd:.2f}>${DAILY_CAP_USD}"}
    return {"ok": True, "spent": round(sp, 4), "remaining": remaining(), "cap": DAILY_CAP_USD}


def record_spend(usd: float, *, provider: str = "gpu_cloud", detail: str = ""):
    """Gerçekleşen harcamayı birleşik ledger'a ekle (tüm sağlayıcılar tek toplam)."""
    led = _load(LEDGER, {})
    led[_today()] = round(float(led.get(_today(), 0.0)) + max(0.0, float(usd)), 4)
    # 30-gün retention
    cutoff = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 31 * 86400))
    _save(LEDGER, {k: v for k, v in led.items() if k >= cutoff})
    print(f"[gpu_budget] +${usd:.3f} {provider} ({detail[:40]}) → bugün ${led[_today()]:.3f}/${DAILY_CAP_USD}", flush=True)


# ── Aynı anda TEK eğitim (training lock) ──
def acquire_training_lock(owner: str) -> dict:
    now = time.time()
    cur = _load(LOCK, {})
    if cur.get("locked") and (now - cur.get("ts", 0)) < TRAIN_LOCK_TTL_S:
        return {"ok": False, "reason": f"başka eğitim sürüyor (lock: {cur.get('owner')})"}
    _save(LOCK, {"locked": True, "owner": owner, "ts": now})
    return {"ok": True}


def release_training_lock():
    _save(LOCK, {"locked": False, "ts": time.time()})


def is_training_locked() -> bool:
    cur = _load(LOCK, {})
    return bool(cur.get("locked")) and (time.time() - cur.get("ts", 0)) < TRAIN_LOCK_TTL_S


def status() -> dict:
    return {
        "daily_cap_usd": DAILY_CAP_USD,
        "spent_today_usd": spent_today(),
        "remaining_usd": remaining(),
        "safe_stop_reached": spent_today() >= DAILY_CAP_USD,
        "training_locked": is_training_locked(),
        "note": "TÜM sağlayıcı toplamı $3/gün; $3'te güvenli-durdurma+checkpoint; aynı anda tek eğitim",
    }
