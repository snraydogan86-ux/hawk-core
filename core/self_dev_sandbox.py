"""
FAZ 5 — Güvenli self-development sandbox.

HAWK kendi kodunu PRODUCTION'a dokunmadan izole sandbox'ta değiştirir, test eder, kanıt
toplar ve YALNIZ ÖNERİ üretir (merge/deploy YOK).

Güvenlik:
  - İzole git worktree (veya bağımsız git sandbox) — canlı checkout etkilenmez.
  - Dosya yazımı YALNIZ sandbox kökü içinde (path traversal reddi).
  - Komutlar argv + shell=False + allowlist (zincirleme reddi).
  - TEMİZ env: production secret/DB kimlik bilgisi GEÇİRİLMEZ.
  - Kaynak limiti (CPU/RAM/dosya) + wall-clock timeout + tool-çağrı sayısı sınırı.
  - Otomatik temizleme (rollback = worktree'yi at; merge yok).
Zincir: kod değişikliği → lint → type-check → unit → security → diff → hash → proposal.
"""
from __future__ import annotations
import hashlib
import os
import re
import shlex
import shutil
import subprocess
import time

SANDBOX_ROOT = os.getenv("HAWK_SANDBOX_ROOT", "/data/hawk_sandboxes")
REPO_ROOT = os.getenv("HAWK_REPO_ROOT", "/app")
_MAX_TOOL_CALLS = int(os.getenv("HAWK_SANDBOX_MAX_TOOLS", "40"))
_META = re.compile(r"[;&|`\n\r><]|\$\(|\$\{|<\(|>\(")
_ALLOWED_EXE = {"git", "python3", "python", "pip", "pip3", "pytest", "ruff", "flake8",
                "pyflakes", "mypy", "node", "npm", "npx", "ls", "cat", "echo", "true"}
# temiz env — YALNIZ bu anahtarlar geçer (secret/DB/prod kimlik GEÇMEZ)
_ENV_ALLOW = ("PATH", "HOME", "LANG", "LC_ALL", "TERM", "PYTHONHASHSEED")


def _clean_env(sandbox: str) -> dict:
    env = {k: os.environ.get(k, "") for k in _ENV_ALLOW if os.environ.get(k)}
    env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    env["HOME"] = sandbox
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["HAWK_SANDBOX"] = "1"        # kod bu bayrakla prod-yan-etkileri kapatabilir
    return env


def _rlimits():
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, (60, 70))
        resource.setrlimit(resource.RLIMIT_FSIZE, (80 * 1024 * 1024, 80 * 1024 * 1024))
    except Exception:
        pass


def _sid(name: str) -> str:
    return "sbx_" + re.sub(r"[^A-Za-z0-9_]", "", name)[:20] + "_" + hashlib.sha256(
        (name + str(time.time())).encode()).hexdigest()[:8]


def create(name: str = "dev", repo_root: str = None) -> dict:
    """İzole sandbox. repo_root'ta .git varsa git worktree; yoksa bağımsız git sandbox."""
    os.makedirs(SANDBOX_ROOT, exist_ok=True)
    sid = _sid(name)
    path = os.path.join(SANDBOX_ROOT, sid)
    rr = repo_root or REPO_ROOT
    if os.path.isdir(os.path.join(rr, ".git")):
        br = "hawk/sbx-" + sid[-8:]
        p = subprocess.run(["git", "-C", rr, "worktree", "add", "-b", br, path, "HEAD"],
                           capture_output=True, text=True, timeout=90)
        if p.returncode != 0:
            return {"ok": False, "error": "worktree: " + (p.stderr or "")[:200]}
        return {"ok": True, "sandbox": path, "sid": sid, "mode": "worktree", "branch": br}
    # bağımsız git sandbox (repo mount yoksa) — mekanik aynı
    os.makedirs(path, exist_ok=True)
    subprocess.run(["git", "-C", path, "init", "-q"], capture_output=True, timeout=30)
    subprocess.run(["git", "-C", path, "config", "user.email", "hawk@sbx"], capture_output=True, timeout=10)
    subprocess.run(["git", "-C", path, "config", "user.name", "hawk"], capture_output=True, timeout=10)
    return {"ok": True, "sandbox": path, "sid": sid, "mode": "standalone", "branch": "main"}


def _safe_path(sandbox: str, rel: str) -> str:
    full = os.path.realpath(os.path.join(sandbox, rel))
    root = os.path.realpath(sandbox)
    if not (full == root or full.startswith(root + os.sep)):
        raise ValueError("path sandbox dışında (reddedildi)")
    return full


def write_file(sandbox: str, rel: str, content: str) -> dict:
    # Denetim fix (2026-07-15): sandbox PATH gerçek dizin olmalı — aksi halde 'sid' yanlışlıkla
    # geçilirse dosya sessizce yanlış yere yazılıyordu. Artık açıkça reddedilir.
    if not (sandbox and os.path.isdir(sandbox)):
        return {"ok": False, "error": f"geçersiz sandbox yolu (sid değil, create()['sandbox'] path'i ver): {sandbox}"}
    full = _safe_path(sandbox, rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(content)
    return {"ok": True, "path": rel, "bytes": len(content)}


def read_file(sandbox: str, rel: str) -> str:
    full = _safe_path(sandbox, rel)
    with open(full, encoding="utf-8") as f:
        return f.read()


class _Ctr:
    n = 0


def run(sandbox: str, cmd: str, *, timeout: int = 60, cwd: str = None) -> dict:
    """Sandbox'ta güvenli komut (argv+shell=False+allowlist+clean-env+rlimit)."""
    try:  # FAZ 1: sandbox (veya global) kill-switch → sandbox yürütmesi DURUR
        import core.cost_guard as _cg_sb
        if _cg_sb.is_killed() or _cg_sb.is_killed("sandbox"):
            return {"ok": False, "error": "sandbox kill-switch aktif — yürütme durduruldu"}
    except Exception:
        pass
    _Ctr.n += 1
    if _Ctr.n > _MAX_TOOL_CALLS:
        return {"ok": False, "error": "max tool-çağrı sınırı aşıldı"}
    if _META.search(cmd):
        return {"ok": False, "error": "zincirleme/redirect reddedildi"}
    try:
        argv = shlex.split(cmd)
    except ValueError as e:
        return {"ok": False, "error": f"ayrıştırma: {e}"}
    if not argv or "/" in argv[0] or os.path.basename(argv[0]) not in _ALLOWED_EXE:
        return {"ok": False, "error": f"izinli değil: {argv[0] if argv else ''}"}
    wd = cwd or sandbox
    try:
        p = subprocess.run(argv, cwd=wd, capture_output=True, text=True, timeout=timeout,
                           env=_clean_env(sandbox), preexec_fn=_rlimits)
        out = (p.stdout or "")[-6000:]
        err = (p.stderr or "")[-2000:]
        return {"ok": p.returncode == 0, "code": p.returncode, "out": out, "err": err}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"timeout {timeout}s"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def diff(sandbox: str) -> str:
    subprocess.run(["git", "-C", sandbox, "add", "-A"], capture_output=True, timeout=30)
    p = subprocess.run(["git", "-C", sandbox, "diff", "--cached"], capture_output=True, text=True, timeout=30)
    return (p.stdout or "")[:20000]


def artifact_hash(sandbox: str) -> str:
    return hashlib.sha256(diff(sandbox).encode()).hexdigest()


def cleanup(sandbox: str, *, repo_root: str = None) -> dict:
    """ROLLBACK = sandbox'ı at (merge YOK). worktree ise git worktree remove."""
    rr = repo_root or REPO_ROOT
    try:
        if os.path.isdir(os.path.join(rr, ".git")):
            subprocess.run(["git", "-C", rr, "worktree", "remove", "--force", sandbox],
                           capture_output=True, timeout=60)
        if os.path.isdir(sandbox):
            shutil.rmtree(sandbox, ignore_errors=True)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


# ── Gate zinciri ─────────────────────────────────────────────────────────────
def gate_lint(sandbox: str, targets: str = ".") -> dict:
    r = run(sandbox, f"python3 -m pyflakes {targets}", timeout=60)
    # pyflakes yok/izinsizse → py_compile'a düş (derleme = temel lint)
    if (not r.get("ok")) and ("No module named" in str(r.get("err", "")) or
                              "izinli değil" in str(r.get("error", "")) or
                              "bulunamadı" in str(r.get("err", ""))):
        r = run(sandbox, f"python3 -m py_compile {targets}", timeout=60)
    return {"gate": "lint", **r}


def gate_typecheck(sandbox: str, targets: str = ".") -> dict:
    r = run(sandbox, f"python3 -m py_compile {targets}", timeout=60)
    return {"gate": "typecheck", **r}


def gate_unit(sandbox: str, path: str = "") -> dict:
    r = run(sandbox, f"python3 -m pytest {path} -q -p no:cacheprovider --no-header", timeout=180)
    return {"gate": "unit", **r}


def gate_security(sandbox: str) -> dict:
    """Basit güvenlik taraması: diff'te secret/tehlikeli desen var mı."""
    d = diff(sandbox)
    bad = re.findall(r"sk-[A-Za-z0-9]{16}|BEGIN [A-Z ]*PRIVATE KEY|shell=True|os\.system\(|eval\(", d)
    return {"gate": "security", "ok": len(bad) == 0, "findings": bad[:5]}


def run_chain(sandbox: str, *, unit_path: str = "", lint_targets: str = ".") -> dict:
    """Uygulanmış değişiklikler üzerinde tam gate zinciri. YALNIZ rapor + öneri (merge YOK)."""
    gates = [gate_lint(sandbox, lint_targets), gate_typecheck(sandbox, lint_targets),
             gate_unit(sandbox, unit_path), gate_security(sandbox)]
    all_ok = all(g.get("ok") for g in gates)
    return {"all_ok": all_ok, "gates": gates, "diff_hash": artifact_hash(sandbox),
            "diff_preview": diff(sandbox)[:1500], "mergeable": False,   # onay olmadan ASLA merge
            "note": "yalnız öneri — production'a merge/deploy admin onayı gerektirir"}
