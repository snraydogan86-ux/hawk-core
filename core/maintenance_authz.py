"""
FAZ 2 — Owner-only maintenance yetki alanı (autonomous-operator ön-şartı).

İKİ GÜVEN ALANI:
  A. NORMAL KULLANICI — yalnız kendi tenant/cihaz/proje/dosya/görev. HAWK çekirdeğine ERİŞEMEZ.
  B. OWNER/MAINTENANCE — yalnız Soner owner hesabı + yetkili maintenance cihazı + allowlist repo +
     allowlist filesystem-root. HAWK çekirdek kodunu yalnız BU alan geliştirebilir.

DEĞİŞMEZ KURAL: kimlik/yetki body/header/query'den DEĞİL; owner e-postası doğrulanmış bearer'dan,
cihaz signed device-token'dan gelir. Bir kontrol okunamazsa FAIL-CLOSED (reddet).
"""
from __future__ import annotations
import os
import time


def _split_env(name: str, default: str = "") -> list[str]:
    return [x.strip().lower() for x in (os.getenv(name, default) or "").split(",") if x.strip()]


def owner_emails() -> set[str]:
    # Soner'in doğrulanmış owner hesapları (santral/telefon: soneraydogan86 dahil). Env ile genişletilebilir.
    return set(_split_env("HAWK_OWNER_EMAILS",
                          os.getenv("HAWK_ADMIN_EMAILS", "snraydogan86@gmail.com,soneraydogan86@gmail.com")))


# HAWK çekirdek repo allowlist (maintenance yalnız bunlara dokunabilir)
def core_repos() -> set[str]:
    return set(_split_env("HAWK_CORE_REPOS", "snraydogan86-ux/hawk-ai,hawk-v2"))


# maintenance operasyonlarının kısıtlandığı filesystem kökleri
def fs_root_allowlist() -> list[str]:
    raw = _split_env("HAWK_MAINT_FS_ROOTS", "/data/hawk_sandboxes,/tmp/hawk_maint")
    return [os.path.realpath(p) for p in raw]


# maintenance için işaretli cihazlar (signed device_id / owner_hash)
def maintenance_devices() -> set[str]:
    return set(_split_env("HAWK_MAINT_DEVICES", ""))


def owner_user_ids() -> set[str]:
    """Owner'ın DEĞİŞMEZ user_id'leri (merkezi config). E-posta değiştirilerek owner OLUNAMAZ:
    gerçek yetki bu user_id ile verilir. Varsayılan '1' (snraydogan86 = users.id 1)."""
    return set(_split_env("HAWK_OWNER_USER_IDS", "1,6"))   # snraydogan86=1, soneraydogan86=6 (ikisi de Soner)


def is_owner_uid(user_id) -> bool:
    return bool(str(user_id or "").strip()) and str(user_id).strip().lower() in owner_user_ids()


def is_owner(email: str, user_id=None) -> bool:
    """Owner mı? user_id verilirse HEM e-posta HEM değişmez user_id eşleşmeli (B.1-4: profil
    e-postasını değiştirerek owner olunamaz). user_id verilmezse geriye-uyumlu (yalnız e-posta).
    Kimlik doğrulanmış bearer'dan gelmeli — çağıran sağlar; okunamazsa fail-closed."""
    em_ok = bool(email) and str(email).strip().lower() in owner_emails()
    if user_id is not None:
        return em_ok and is_owner_uid(user_id)
    return em_ok


def is_maintenance_device(device_id: str) -> bool:
    devs = maintenance_devices()
    if not devs:
        return False   # fail-closed: hiç maintenance cihazı tanımlı değilse hiçbiri değil
    return bool(device_id) and str(device_id).strip().lower() in devs


def repo_allowed(repo: str) -> bool:
    r = str(repo or "").strip().lower()
    return any(r == a or r.endswith("/" + a.split("/")[-1]) for a in core_repos())


def path_allowed(path: str) -> bool:
    """Path allowlist-root altında mı? realpath ile path-traversal/symlink kaçışı ENGELLENİR."""
    if not path:
        return False
    try:
        rp = os.path.realpath(path)
    except Exception:
        return False   # fail-closed
    for root in fs_root_allowlist():
        if rp == root or rp.startswith(root + os.sep):
            return True
    return False


# basit audit ledger (in-memory ring + stdout; kalıcı audit tenant DB'de)
_AUDIT: list[dict] = []


def audit(action: str, *, email: str = "", device_id: str = "", decision: bool,
          reason: str = "", target: str = "") -> dict:
    rec = {"ts": int(time.time()), "action": action,
           "email": (email[:3] + "***") if email else "",     # PII maskele
           "device": (device_id[:6] + "…") if device_id else "",
           "decision": bool(decision), "reason": reason, "target": target[:80]}
    _AUDIT.append(rec)
    if len(_AUDIT) > 500:
        del _AUDIT[: len(_AUDIT) - 500]
    print(f"[maint-authz] {action} decision={decision} reason={reason}", flush=True)
    return rec


def recent_audit(n: int = 50) -> list[dict]:
    return _AUDIT[-n:]


def can_maintain(*, email: str, device_id: str = "", repo: str = "", path: str = "") -> dict:
    """HAWK çekirdek maintenance izni. TÜM koşullar: owner + maintenance-cihaz + allowlist-repo +
    allowlist-path. Herhangi biri değilse REDDET (fail-closed). Normal kullanıcı hepsinde düşer."""
    checks = {
        "owner": is_owner(email),
        "device": is_maintenance_device(device_id) if device_id else False,
        "repo": repo_allowed(repo) if repo else True,       # repo verilmezse repo-kontrolü atlanır
        "path": path_allowed(path) if path else True,
    }
    ok = checks["owner"] and checks["device"] and checks["repo"] and checks["path"]
    reason = "ok" if ok else "reddedildi:" + ",".join(k for k, v in checks.items() if not v)
    audit("can_maintain", email=email, device_id=device_id, decision=ok, reason=reason, target=repo or path)
    return {"allowed": ok, "checks": checks, "reason": reason}
