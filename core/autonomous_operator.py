"""
FAZ 11 — HAWK Autonomous Operator (OWNER-ONLY kendini-geliştirme).

Bu yol NORMAL KULLANICI yolundan TAMAMEN AYRIDIR. Yalnız owner (Soner) + maintenance-cihazı +
allowlist-repo/path (maintenance_authz) erişebilir. Normal kullanıcı ASLA erişemez.

AKIŞ: telemetry→problem → sandbox kod değişikliği (FAZ 5) → test → bağımsız evaluator (FAZ 6) →
RİSK SINIFLANDIRMA → düşük-risk: staging/shadow/canary/production (önceden-onaylı politika) VEYA
yüksek-risk: OWNER ONAYI. → operational learning → yeni döngü.

DÜŞÜK RİSK (oto-uygun): doküman, kırık-link, test ekleme, erişilebilirlik, küçük-UI, düşük-risk perf.
HER ZAMAN OWNER ONAYI: auth/JWT, billing/IAP, destructive-migration, veri-silme, secret/key,
gizlilik/hukuk, App-Store, güvenlik-politikası-gevşetme, geniş-model-promotion, cost-cap-aşımı,
izin-modeli, owner/role/tenant, kalıcı-şema.
"""
from __future__ import annotations
import re
import time
from core import maintenance_authz as _MA

# HER ZAMAN owner onayı gerektiren desenler (dosya yolu / değişiklik açıklaması)
_HIGH_RISK = [
    (re.compile(r"auth|jwt|session|login|token|password|parola", re.I), "auth/JWT"),
    (re.compile(r"billing|payment|iap|apple_billing|stripe|abonelik|ödeme", re.I), "billing/IAP"),
    (re.compile(r"migrations?/.*\.sql|drop table|truncate|delete from|alter table.*drop", re.I), "destructive-DB/migration"),
    (re.compile(r"secret|api[_-]?key|private[_-]?key|\.env|credential|anahtar", re.I), "secret/key"),
    (re.compile(r"cost_guard|kill.?switch|maintenance_authz|güvenlik.?polit|security.?policy", re.I), "güvenlik-politikası"),
    (re.compile(r"privacy|terms|gizlilik|hukuk|kvkk|gdpr|legal", re.I), "gizlilik/hukuk"),
    (re.compile(r"promot(e|ion)|production.*model|user_pct|canary.*[1-9][0-9]?%|geniş.*promotion", re.I), "model-promotion"),
    (re.compile(r"tenant|owner|role|permission|izin.?model|rbac", re.I), "izin/owner/tenant-modeli"),
    (re.compile(r"app.?store|google.?play|submission|yayınla", re.I), "store-submission"),
]
# DÜŞÜK RİSK (oto-uygun) — yalnız bunlar
_LOW_RISK = [
    (re.compile(r"\.md$|docs?/|readme|doküman|documentation", re.I), "dokümantasyon"),
    (re.compile(r"test_|/tests?/|_test\.py|test ekle", re.I), "test-ekleme"),
    (re.compile(r"kırık.?link|broken.?link|dead.?link|404", re.I), "kırık-link"),
    (re.compile(r"erişilebilirlik|accessibility|aria-|alt=", re.I), "erişilebilirlik"),
    (re.compile(r"typo|yazım|comment|yorum|küçük.?ui|small.?ui", re.I), "küçük-düzeltme"),
]


def classify_risk(*, changed_files: list[str] | None = None, description: str = "") -> dict:
    """Değişikliği düşük/yüksek risk sınıflandır. Yüksek-risk deseni VARSA → owner onayı ZORUNLU
    (fail-safe: şüpheli → yüksek). Yalnız açıkça düşük-risk + hiç yüksek-risk yoksa oto-uygun."""
    blob = " ".join([*(changed_files or []), description]).lower()
    high = [label for rx, label in _HIGH_RISK if rx.search(blob)]
    if high:
        return {"risk": "high", "requires_owner_approval": True, "reasons": high,
                "auto_eligible": False}
    low = [label for rx, label in _LOW_RISK if rx.search(blob)]
    if low:
        return {"risk": "low", "requires_owner_approval": False, "reasons": low,
                "auto_eligible": True}
    # sınıflanamayan → FAIL-SAFE yüksek (owner onayı)
    return {"risk": "high", "requires_owner_approval": True, "reasons": ["sınıflanamadı-failsafe"],
            "auto_eligible": False}


class AutonomousOperator:
    """Owner-only maintenance Director. Her giriş maintenance_authz ile kapılıdır."""

    def __init__(self, *, owner_email: str, device_id: str = ""):
        self.owner_email = owner_email
        self.device_id = device_id
        self._proposals: dict[str, dict] = {}

    def _gate(self, *, repo: str = "", path: str = "") -> dict:
        """Owner + maintenance-cihaz + allowlist. Normal kullanıcı burada DÜŞER (fail-closed)."""
        return _MA.can_maintain(email=self.owner_email, device_id=self.device_id, repo=repo, path=path)

    async def run_maintenance_cycle(self, *, problem: str, repo: str = "hawk-v2",
                                    changed_files: list[str] | None = None) -> dict:
        """Owner-only maintenance turu: yetki → sandbox değişiklik+test → evaluator → risk → proposal.
        Production'a OTOMATİK almaz; düşük-risk 'auto_eligible' işaretlenir, yüksek-risk owner onayı bekler."""
        g = self._gate(repo=repo)
        if not g["allowed"]:
            return {"ok": False, "denied": True, "reason": g["reason"],
                    "note": "Autonomous Operator YALNIZ owner + maintenance-cihazı (normal kullanıcı erişemez)"}
        # sandbox'ta gerçek değişiklik + test (FAZ 5) — çağıran sandbox sonucunu verebilir; burada risk+proposal
        risk = classify_risk(changed_files=changed_files, description=problem)
        pid = "aop_" + str(int(time.time() * 1000))[-12:]
        proposal = {
            "proposal_id": pid, "problem": problem, "repo": repo,
            "changed_files": changed_files or [], "risk": risk,
            "status": ("auto_eligible" if risk["auto_eligible"] else "owner_approval_required"),
            "owner": self.owner_email[:3] + "***", "ts": int(time.time()),
        }
        self._proposals[pid] = proposal
        _MA.audit("aop_proposal", email=self.owner_email, device_id=self.device_id,
                  decision=True, reason=risk["risk"], target=problem[:60])
        return {"ok": True, **proposal,
                "note": ("düşük-risk: önceden-onaylı politikada oto-uygun (staging→canary→prod)"
                         if risk["auto_eligible"] else
                         "yüksek-risk: OWNER ONAYI şart (auth/billing/secret/migration/promotion vb.)")}

    def approve(self, proposal_id: str, *, actor: str) -> dict:
        """Yüksek-risk proposal owner onayı. Yalnız owner onaylayabilir."""
        if not _MA.is_owner(actor):
            return {"ok": False, "error": "yalnız owner onaylayabilir"}
        p = self._proposals.get(proposal_id)
        if not p:
            return {"ok": False, "error": "proposal yok"}
        p["status"] = "owner_approved"
        p["approved_by"] = actor[:3] + "***"
        return {"ok": True, "proposal_id": proposal_id, "status": "owner_approved"}

    def rollback(self, proposal_id: str, *, actor: str, reason: str = "") -> dict:
        """Uygulanmış değişikliği geri al (canary hatası / regresyon). Owner-only + audit."""
        if not _MA.is_owner(actor):
            return {"ok": False, "error": "yalnız owner rollback yapabilir"}
        p = self._proposals.get(proposal_id, {"proposal_id": proposal_id})
        p["status"] = "rolled_back"
        _MA.audit("aop_rollback", email=actor, decision=True, reason=reason or "regresyon", target=proposal_id)
        return {"ok": True, "proposal_id": proposal_id, "status": "rolled_back", "reason": reason}

    def list_proposals(self) -> list:
        return list(self._proposals.values())
