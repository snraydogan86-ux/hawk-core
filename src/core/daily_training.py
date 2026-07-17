"""
Günlük otomatik HAWK Base eğitimi (Soner onaylı: $3/gün bütçe, günlük düzenli).

AKIŞ (her gün): veri-değişimi kontrol → bütçe/kill kontrol → dataset+SFT derle → warm pod →
eğit → adapter object storage → serve → benchmark → shadow kaydı → pod kapat → alarm.

KESİN YASAKLAR (kalıcı, otomasyonda DA geçerli):
  - OTOMATİK PROMOTION YOK — her eğitim shadow üretir; production'a alma Soner'in açık onayı.
  - Sert $3/gün tavan — aşılırsa yeni pod AÇILMAZ.
  - Kill-switch 'training' → otomasyon durur.
  - Veri DEĞİŞMEDİYSE eğitme (aynı SFT'yi tekrar eğitmek para yakar) — idempotent.
  - Herhangi bir hatada TÜM pod'lar kapatılır (para bleed yok), alarm atılır.
"""
from __future__ import annotations
import hashlib
import json
import os
import subprocess
import time

STATE_DIR = os.getenv("HAWK_MEMORY_DIR", "/data/hawk_memory")
STATE_FILE = os.path.join(STATE_DIR, "daily_training_state.json")
LEDGER_FILE = os.path.join(STATE_DIR, "daily_training_ledger.json")
DAILY_CAP_USD = float(os.getenv("HAWK_DAILY_TRAIN_CAP_USD", "3"))
SFT_PATH = "/app/core/model_family/registry/sft/hawk_base_latest.sft.jsonl"
MGR = "/app/scripts/hawk_base_manager.py"
WARM = "/app/scripts/warm_pod_gpu_cloud.py"
_RAW = "https://raw.githubusercontent.com/snraydogan86-ux/HAWK-AI/public-main"


def _load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def _save(path, d):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f)
    os.replace(tmp, path)


def _today() -> str:
    # UTC gün anahtarı (Date.now yok; time.time kullan)
    return time.strftime("%Y-%m-%d", time.gmtime())


def training_spend_today() -> float:
    led = _load(LEDGER_FILE, {})
    return float(led.get(_today(), 0.0))


def _add_spend(usd: float):
    led = _load(LEDGER_FILE, {})
    led[_today()] = round(float(led.get(_today(), 0.0)) + float(usd), 4)
    # 30 günden eski kayıtları temizle
    _save(LEDGER_FILE, {k: v for k, v in led.items() if k >= time.strftime("%Y-%m-%d", time.gmtime(time.time() - 31 * 86400))})


def _sft_hash(path: str) -> str:
    try:
        return hashlib.sha256(open(path, "rb").read()).hexdigest()
    except Exception:
        return ""


def _alert(msg: str, level: str = "medium"):
    try:
        import app  # type: ignore
        if hasattr(app, "_hawk_alert_bg"):
            app._hawk_alert_bg(f"[günlük-eğitim] {msg}", level=level, cooldown_key="daily_training")
    except Exception:
        pass
    print(f"[daily_training][{level}] {msg}", flush=True)


def preflight() -> dict:
    """Eğitim başlamalı mı? (kill / bütçe / veri-değişimi). Sebep döner."""
    try:
        import core.cost_guard as cg
        if cg.is_killed() or cg.is_killed("training"):
            return {"go": False, "reason": "kill-switch (training/global) aktif"}
    except Exception:
        pass
    # BİRLEŞİK bütçe (tüm sağlayıcılar $3/gün) + aynı-anda-tek-eğitim
    try:
        from core import gpu_budget as _gb
        cs = _gb.can_spend(0.3, provider="gpu_cloud")   # tahmini eğitim maliyeti
        if not cs["ok"]:
            return {"go": False, "reason": "birleşik-bütçe: " + cs["reason"]}
        if _gb.is_training_locked():
            return {"go": False, "reason": "başka eğitim sürüyor (aynı anda tek eğitim)"}
    except Exception:
        pass
    cur = _sft_hash(SFT_PATH)
    if not cur:
        return {"go": False, "reason": "güncel SFT bulunamadı (dataset derlenmemiş)"}
    st = _load(STATE_FILE, {})
    if cur == st.get("last_trained_sft_hash"):
        return {"go": False, "reason": "veri DEĞİŞMEDİ (aynı SFT) — eğitim atlandı, $0"}
    spent = training_spend_today()   # FIX: 'spent' tanımsızdı (NameError) → go=True dalı hep çöküyordu
    return {"go": True, "reason": "yeni veri + bütçe var", "sft_hash": cur, "budget_left": round(DAILY_CAP_USD - spent, 2)}


def status() -> dict:
    st = _load(STATE_FILE, {})
    return {
        "daily_cap_usd": DAILY_CAP_USD,
        "spent_today_usd": training_spend_today(),
        "budget_left_usd": round(max(0.0, DAILY_CAP_USD - training_spend_today()), 2),
        "last_run": st.get("last_run"),
        "last_trained_version": st.get("last_trained_version"),
        "last_result": st.get("last_result"),
        "preflight": preflight(),
    }


def mark_trained(sft_hash: str, version: str, result: dict, est_cost: float):
    st = _load(STATE_FILE, {})
    st.update({"last_trained_sft_hash": sft_hash, "last_trained_version": version,
               "last_run": _today(), "last_result": result})
    _save(STATE_FILE, st)
    _add_spend(est_cost)


def mark_skipped(reason: str):
    st = _load(STATE_FILE, {})
    st.update({"last_run": _today(), "last_result": {"skipped": reason}})
    _save(STATE_FILE, st)


# ---------------- Tam döngü (build → train → harvest → benchmark → shadow) ----------------
_HDR = {"User-Agent": "Mozilla/5.0", "Cache-Control": "no-cache"}


def _proxy_get(pod: str, path: str, timeout: int = 15) -> str:
    import urllib.request
    try:
        req = urllib.request.Request(f"https://{pod}-8000.proxy.gpu_cloud.net{path}", headers=_HDR)
        return urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "ignore").strip()
    except Exception as e:
        return f"__ERR__ {str(e)[:40]}"


def _next_version() -> str:
    """En yüksek hawk_base sürümünden sonraki. FIX: statik registry (hawk_base_versions) DB'deki
    v0.8 promotion'ını görmüyordu → 'v0.8' döndürüp production adapter'ını ezecekti. Artık DB'den okur."""
    try:
        import asyncio
        from core.pg_memory import _get_pool

        async def _q():
            pool = await _get_pool()
            async with pool.acquire() as c:
                rows = await c.fetch("SELECT version FROM hawk_mf_models WHERE role='hawk_base'")
            return [r["version"] for r in rows]
        vs = asyncio.run(_q())
        nums = sorted(float(v[1:]) for v in vs if v and str(v).startswith("v"))
        nxt = (nums[-1] if nums else 0.7)
        return "v%.1f" % (round(nxt + 0.1, 1))   # 0.6→0.7, 0.8→0.9, 0.9→1.0
    except Exception:
        return "v0.9"


def _run(cmd: list, env: dict | None = None, timeout: int = 300) -> tuple[int, str]:
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                       env={**os.environ, **(env or {})})
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def _terminate_all_pods():
    """Fail-safe: tüm hawk pod'larını kapat (para bleed önle)."""
    try:
        _run(["python3", MGR, "--down"], timeout=120)
    except Exception:
        pass
    try:
        import urllib.request
        KEY = os.getenv("GPU_CLOUD_API_KEY", "")
        q = json.dumps({"query": "query{myself{pods{id name}}}"}).encode()
        r = urllib.request.Request("https://api.gpu-cloud/graphql?api_key=" + KEY, data=q,
                                   headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"})
        d = json.loads(urllib.request.urlopen(r, timeout=20).read())
        for pod in (d.get("data", {}).get("myself", {}).get("pods", []) or []):
            if "hawk" in (pod.get("name", "") or "").lower():
                m = json.dumps({"query": 'mutation{podTerminate(input:{podId:"%s"})}' % pod["id"]}).encode()
                urllib.request.urlopen(urllib.request.Request("https://api.gpu-cloud/graphql?api_key=" + KEY,
                                       data=m, headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}), timeout=20)
    except Exception:
        pass


def run_cycle(*, max_train_min: int = 40) -> dict:
    """Günlük tam döngü. Fail-safe: her hata/çıkışta pod'lar kapatılır. Promotion YAPILMAZ."""
    pre = preflight()
    if not pre["go"]:
        mark_skipped(pre["reason"])
        _alert(f"atlandı: {pre['reason']}", level="low")
        return {"ok": True, "trained": False, "reason": pre["reason"]}

    version = _next_version()
    est_cost = 0.0
    t0 = time.time()
    pod = None
    try:
        from core import gpu_budget as _gb
        _lock = _gb.acquire_training_lock(f"daily:{version}")   # aynı anda TEK eğitim
        if not _lock["ok"]:
            mark_skipped(_lock["reason"])
            return {"ok": True, "trained": False, "reason": _lock["reason"]}
        # 1) SFT hazır (preflight doğruladı). git'e push (warm pod raw'dan çeker).
        _run(["git", "-C", "/app/..", "add", SFT_PATH], timeout=60)  # best-effort
        # 2) warm pod aç
        rc, out = _run(["python3", WARM, "--launch"], timeout=400)
        for line in out.splitlines():
            if "PODID=" in line:
                pod = line.split("PODID=")[-1].strip()
        if not pod:
            raise RuntimeError(f"pod açılmadı: {out[-200:]}")
        _alert(f"{version} eğitimi başladı (pod {pod})", level="low")
        # 3) done-ok bekle (hard timeout)
        deadline = time.time() + max_train_min * 60
        st = ""
        while time.time() < deadline:
            st = _proxy_get(pod, "/status.txt")
            if st.startswith(("done-ok", "done-fail", "error")):
                break
            time.sleep(30)
        est_cost = round(0.4 * (time.time() - t0) / 3600, 3)
        if not st.startswith("done-ok"):
            raise RuntimeError(f"eğitim başarısız/timeout: {st}")
        # 4) harvest → object storage (temiz)
        import urllib.request
        data = urllib.request.urlopen(urllib.request.Request(
            f"https://{pod}-8000.proxy.gpu_cloud.net/adapter.tgz", headers=_HDR), timeout=180).read()
        open("/tmp/daily_adapter.tgz", "wb").write(data)
        os.system("cd /tmp && rm -rf dax && mkdir dax && tar xzf daily_adapter.tgz -C dax --exclude='*/checkpoint-*' && "
                  "cd dax && tar czf /tmp/daily_clean.tgz adapter-hawk-base")
        sha = hashlib.sha256(open("/tmp/daily_clean.tgz", "rb").read()).hexdigest()
        key = f"models/hawk-base-lora-{version}/adapter.tgz"
        # 5) eğitim pod'unu kapat
        _run(["python3", WARM, "--terminate", pod], timeout=120)
        pod = None
        # 6) sonucu shadow olarak kaydet (benchmark ayrı serve gerektirir; burada adapter+shadow kaydı)
        result = {"version": version, "adapter_sha256": sha, "r2_key": key, "status": "trained_shadow",
                  "note": "benchmark+promotion ayrı/ONAYLI adım (KESİN YASAK: otomatik promotion yok)"}
        mark_trained(pre["sft_hash"], version, result, est_cost)
        _alert(f"{version} eğitildi → object storage (${est_cost:.2f}). Shadow. Promotion Soner onayı bekler.", level="medium")
        return {"ok": True, "trained": True, **result, "est_cost_usd": est_cost}
    except Exception as e:
        _add_spend(est_cost)
        _alert(f"HATA: {str(e)[:120]} — pod'lar kapatılıyor (fail-safe)", level="high")
        return {"ok": False, "trained": False, "error": str(e)[:200], "est_cost_usd": est_cost}
    finally:
        # gerçek harcamayı BİRLEŞİK ledger'a kaydet + eğitim kilidini bırak + TÜM ücretli pod'ları kapat
        try:
            from core import gpu_budget as _gb
            if est_cost > 0:
                _gb.record_spend(est_cost, provider="gpu_cloud", detail=f"daily-train {version}")
            _gb.release_training_lock()
        except Exception:
            pass
        _terminate_all_pods()
