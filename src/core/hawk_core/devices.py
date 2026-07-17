"""
HAWK CORE — Güvenli Cihaz (PC) Ajanı sunucu tarafı.

Akış (güvenli):
  1. HAWK bir cihaza komut KUYRUKLAR (queue_command).
  2. Risk kapısı: tehlikeli komut → BLOCKED; kritik → Soner telefonla aranır (pending);
     düşük risk → otomatik approved.
  3. Cihazdaki ajan poll_commands ile SADECE approved komutları alır, çalıştırır,
     submit_result ile çıktıyı döner.
Onaysız/bloklu komut cihaza ASLA gitmez. Her şey loglanır.
"""
from __future__ import annotations

import hashlib
import re
import secrets
from typing import Any, Dict, List, Optional


def _hash_token(token: str) -> str:
    """Token'ın SHA-256'sı — DB'de düz metin token saklamamak için."""
    return hashlib.sha256((token or "").encode()).hexdigest()

from core.pg_memory import _get_pool
from . import memory as M
from .policy import classify

# Cihazda da, sunucuda da KESİNLİKLE reddedilen geri-dönülemez/yıkıcı komutlar.
# Normalize edilmiş (küçük harf + tek boşluk) metne karşı regex — substring kaçışlarını kapatır
# (ör. "rm -fr /", "rm  -rf  --no-preserve-root /", "curl x | sh", reverse shell).
_HARD_BLOCK_RE = [
    (r"\brm\s+-[a-z]*[rf][a-z]*\s+(--no-preserve-root\s+)?(/|/\*|~|\$home)(\s|/|\*|$)", "kök/ev dizini silme"),
    (r":\s*\(\s*\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:", "fork bomb"),
    (r"\bmkfs(\.\w+)?\b|\bwipefs\b", "dosya sistemi biçimlendirme"),
    (r"\bdd\b[^|\n]*\bof=/dev/(sd|nvme|hd|vd|mmcblk|disk)", "diske ham yazma"),
    (r">\s*/dev/(sd|nvme|hd|vd|mmcblk|disk)", "blok aygıtına yönlendirme"),
    (r"\b(shutdown|reboot|halt|poweroff)\b|\binit\s+[06]\b", "sistemi kapatma/yeniden başlatma"),
    (r"\b(curl|wget|fetch)\b[^|\n]*\|\s*(sudo\s+)?(sh|bash|zsh|ksh|python[0-9.]*|perl|ruby|node)\b",
     "internetten indirip doğrudan çalıştırma"),
    (r"/dev/tcp/|\bnc\b\s+-[a-z]*e\b|\bncat\b[^|\n]*-e\b|bash\s+-i\s*>?&", "ters kabuk (reverse shell)"),
    (r"\bchmod\s+-r\s*0*00\s+/(\s|$)|\bchown\s+-r\s+\w+\s+/(\s|$)", "kök izin/sahiplik kırma"),
    (r"\biptables\s+-f\b|\bufw\s+disable\b|\bsystemctl\s+stop\s+(ssh|sshd|firewalld)\b", "güvenlik/erişim devre dışı"),
    (r"\bdrop\s+database\b|\bdrop\s+table\b|\btruncate\s+(table\s+)?\w+", "veritabanı kalıcı silme"),
    (r"\bdelete\s+from\s+\w+\s*(;|$)", "koşulsuz (WHERE'siz) DELETE"),
    (r"\bformat\s+[a-z]:|\bdel\s+/[fsq]|\brd\s+/s\b", "windows yıkıcı silme"),
    # Gizli dosya / özel anahtar OKUMA — .env, ssh anahtarları, sertifikalar, cloud kimlik bilgileri.
    # .env.example / .sample / .template gibi ŞABLONLAR hariç (negatif ileri-bakış).
    (r"\b(cat|type|less|more|head|tail|nano|vim?|emacs|strings|xxd|od|bat|get-content|gc)\b[^|\n]*"
     r"(\.env\b(?!\.(example|sample|template|dist|md))|/\.ssh/|\bid_rsa\b|\bid_ed25519\b|\bid_dsa\b|"
     r"\bid_ecdsa\b|\.pem\b|\.pfx\b|\.p12\b|\.pgpass\b|\.npmrc\b|/\.aws/|/\.gnupg/|"
     r"\bprivate[_-]?key\b|\bsecrets?\.(json|ya?ml|env|txt))",
     "gizli dosya/özel anahtar okuma"),
    # Secret DIŞARI sızdırma — .env / anahtar / kimlik bilgisini ağ üstünden gönderme.
    (r"\b(curl|wget|nc|ncat|scp|rsync|ftp|sftp|invoke-webrequest|iwr|invoke-restmethod|irm)\b[^|\n]*"
     r"(\.env\b|\bid_rsa\b|\.pem\b|/\.ssh/|\.pgpass\b|/\.aws/|\bsecret)",
     "secret dışarı sızdırma"),
    # Process substitution ile indir-çalıştır: bash <(curl ...) / source <(wget ...)
    (r"\b(bash|sh|zsh|ksh|dash|python[0-9.]*|perl|ruby|node|source|eval)\b[^|\n]*<\(\s*(curl|wget|fetch)\b",
     "internetten indirip çalıştırma (process substitution)"),
    # find ile toplu/geri-dönülemez silme veya yıkıcı -exec
    (r"\bfind\b[^|\n]*\s-delete\b", "find ile toplu silme"),
    (r"\bfind\b[^|\n]*-exec[a-z]*\s+(sudo\s+)?(rm|shred|mkfs|dd|unlink)\b", "find -exec yıkıcı komut"),
    # Blok aygıtına tee ile ham yazma (dd of= zaten üstte)
    (r"\btee\b[^|\n]*\s/dev/(sd|nvme|hd|vd|mmcblk|disk)", "blok aygıtına tee ile yazma"),
    # Kritik kimlik/parola dosyasını EZME (>, >>, tee)
    (r"(>>?|\btee\b[^|\n]*)\s*/etc/(passwd|shadow|gshadow|sudoers)\b",
     "kritik sistem dosyasını ezme"),
    # Parola/kimlik dosyalarını OKUMA/KOPYALAMA (shadow/gshadow/sudoers)
    (r"\b(cat|less|more|head|tail|strings|xxd|od|cp|mv|scp|rsync|tar|dd|nano|vim?|emacs|bat)\b[^|\n]*"
     r"/etc/(shadow|gshadow|sudoers)\b",
     "parola/kimlik dosyası (shadow/sudoers) erişimi"),
    # Ortam değişkeni / secret'ı ağ üstünden dışarı gönderme (env | curl ...)
    (r"\b(env|printenv|set)\b[^|\n]*\|\s*(curl|wget|nc|ncat|scp|ftp|sftp|invoke-restmethod|irm|iwr)\b",
     "ortam değişkeni/secret dışarı sızdırma"),
    # Kritik kimlik dosyasında izin/sahiplik değiştirme
    (r"\b(chmod|chown|chgrp)\b[^|\n]*/etc/(shadow|passwd|gshadow|sudoers)\b",
     "kritik kimlik dosyasında izin/sahiplik değişimi"),
]

# Cihaz (uzak worker) komutlarında EK ONAY isteyen desenler — yıkıcı DEĞİL ama kullanıcı onayı şart
# (Md.12: deploy/git push/npm install/docker restart/db değişikliği). HAWK'ın KENDİ otonomisini
# (policy.classify → HAWK'ın kendi sunucu işlemleri) ETKİLEMEZ; yalnız queue_command (uzak cihaz) yolunda.
_DEVICE_APPROVE_RE = [
    (r"\b(npm|yarn|pnpm|bun)\s+(install|add|i|remove|rm|uninstall|update|upgrade|ci)\b",
     "paket kurulumu/değişikliği (npm/yarn/pnpm)"),
    (r"\b(pip|pip3|pipx|poetry|conda)\s+(install|uninstall|remove|add|update)\b", "python paket kurulumu (pip)"),
    (r"\bapt(-get)?\s+(install|remove|purge|upgrade)\b|\b(brew|choco|winget|scoop)\s+(install|uninstall|upgrade)\b",
     "sistem paketi kurulumu"),
    (r"\bgem\s+install\b|\bcargo\s+(install|add)\b|\bgo\s+install\b|\bdotnet\s+add\b", "paket kurulumu"),
    (r"\brm\b(?!\s+-[a-z]*[rf])|\bdel\b(?!\s+/[fsq])|\berase\b|\bremove-item\b|\bunlink\b|\brimraf\b", "dosya silme"),
    (r"\bgit\s+commit\b|\bgit\s+merge\b|\bgit\s+rebase\b|\bgit\s+reset\s+--hard\b", "git commit/merge (onay)"),
]


def _strip_write_body(cmd: str) -> str:
    """Dosya-yazımı gövdesini (heredoc/here-string) çıkar — onay taraması sadece GERÇEK komuta baksın.
    'cat > README <<EOF ... npm install ... EOF' yazımı, içeriğinde geçse de npm ÇALIŞTIRMIYOR."""
    c = cmd or ""
    # SADECE gövdeyi (heredoc başlık satırından SONRAKİ içerik) çıkar; başlık satırındaki
    # yönlendirme/pipe (ör. '> /dev/sda') KORUNUR — yoksa 'cat <<EOF > /dev/sda ... EOF'
    # yıkıcı redirect'i gövdeyle birlikte silinir ve denetimi atlatırdı.
    c = re.sub(r"<<-?\s*['\"]?(\w+)['\"]?([^\n]*)\n.*?\n\1",
               lambda m: " " + m.group(2) + " <written> ", c, flags=re.S)
    c = re.sub(r"@(['\"]).*?\1@", " <written> ", c, flags=re.S)
    return c


def device_needs_approval(cmd: str) -> Optional[str]:
    """Uzak cihaz komutu ek onay gerektiriyorsa Türkçe sebep döner (yıkıcı değil ama onaylı).
    Dosya-yazımı GÖVDESİ taranmaz (heredoc içeriği komut değildir)."""
    c = re.sub(r"\s+", " ", _strip_write_body(cmd or "").strip().lower())
    for pat, reason in _DEVICE_APPROVE_RE:
        if re.search(pat, c):
            return reason
    return None


def block_reason(cmd: str) -> Optional[str]:
    """Yıkıcı komutsa Türkçe sebep döner, değilse None. Normalize edip regex uygular."""
    c = re.sub(r"\s+", " ", (cmd or "").strip().lower())
    for pat, reason in _HARD_BLOCK_RE:
        if re.search(pat, c):
            return reason
    return None


def _blocked(cmd: str) -> bool:
    return block_reason(cmd) is not None


async def register_device(name: str, platform: str = "", email: Optional[str] = None) -> Dict[str, Any]:
    """Cihaz kaydı. Token yalnız BİR KEZ döner; DB'de sadece hash saklanır (düz metin yok)."""
    token = "hdev_" + secrets.token_urlsafe(24)
    pool = await _get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO hawk_devices (name, token_hash, platform, email) VALUES ($1,$2,$3,$4)",
            name[:100], _hash_token(token), platform[:50], (email or None))
    await M.remember_episode("event", f"[device] yeni cihaz kaydı: {name} (sahip: {email or 'admin'})")
    return {"ok": True, "name": name, "token": token, "email": email}


async def list_devices(email: Optional[str] = None) -> List[Dict[str, Any]]:
    """email verilirse SADECE o kullanıcının cihazları (multi-tenant izolasyon)."""
    pool = await _get_pool()
    async with pool.acquire() as conn:
        if email:
            rows = await conn.fetch(
                "SELECT id, name, platform, email, workspace, last_seen, created_at FROM hawk_devices "
                "WHERE email=$1 AND revoked_at IS NULL ORDER BY id DESC", email)
        else:
            rows = await conn.fetch(
                "SELECT id, name, platform, email, workspace, last_seen, created_at FROM hawk_devices "
                "WHERE revoked_at IS NULL ORDER BY id DESC")
        return [dict(r) for r in rows]


async def set_workspace(name: str, workspace: str, email: Optional[str] = None) -> Dict[str, Any]:
    """Cihazın izinli çalışma klasörünü (workspace) ayarla. Worker komutları BU klasörde çalıştırır
    ve dışına çıkmaz. email verilirse SADECE kendi cihazı (multi-tenant)."""
    ws = (workspace or "").strip()[:500] or None
    pool = await _get_pool()
    async with pool.acquire() as conn:
        if email is not None:
            res = await conn.execute("UPDATE hawk_devices SET workspace=$1 WHERE name=$2 AND email=$3", ws, name, email)
        else:
            res = await conn.execute("UPDATE hawk_devices SET workspace=$1 WHERE name=$2", ws, name)
    n = 0
    try:
        n = int(str(res).split()[-1])
    except Exception:
        n = 0
    return {"ok": n > 0, "workspace": ws, "error": (None if n > 0 else "not_found")}


async def cancel_pending(name: str, email: Optional[str] = None) -> Dict[str, Any]:
    """DURDUR: cihazın kuyruğundaki henüz alınmamış komutları (pending/approved) iptal et.
    Zaten 'sent' (worker'a gitmiş) komut durdurulamaz — çalışıyor olabilir. email → kendi cihazı."""
    pool = await _get_pool()
    async with pool.acquire() as conn:
        if email is not None:
            res = await conn.execute(
                "UPDATE hawk_device_commands SET status='cancelled', updated_at=now() "
                "WHERE device=$1 AND email=$2 AND status IN ('pending','approved')", name, email)
        else:
            res = await conn.execute(
                "UPDATE hawk_device_commands SET status='cancelled', updated_at=now() "
                "WHERE device=$1 AND status IN ('pending','approved')", name)
    n = 0
    try:
        n = int(str(res).split()[-1])
    except Exception:
        n = 0
    await M.remember_episode("action", f"[device] DURDUR: {n} bekleyen komut iptal @ {name}")
    return {"ok": True, "cancelled": n}


async def delete_device(name: str, email: Optional[str] = None) -> Dict[str, Any]:
    """Cihazı KALDIR — SOFT-DELETE (revoke). Cihaz kaydı revoked_at ile işaretlenir, token iptal edilir;
    KOMUT + AUDIT GEÇMİŞİ KORUNUR (done/failed kayıtları SİLİNMEZ). UI'da aktif görünmez; bekleyen
    komutlar iptal edilir. email verilirse SADECE o kullanıcının cihazı (multi-tenant). Döner: {ok, revoked}.
    (Eski davranış hem cihazı hem TÜM komut geçmişini siliyordu → denetim kanıtı uçuyordu; düzeltildi.)"""
    if not name:
        return {"ok": False, "error": "name_required", "revoked": 0, "deleted": 0}
    pool = await _get_pool()
    async with pool.acquire() as conn:
        if email:
            res = await conn.execute(
                "UPDATE hawk_devices SET revoked_at=now(), token=NULL, token_hash=NULL "
                "WHERE name=$1 AND email=$2 AND revoked_at IS NULL", name[:100], email)
        else:
            res = await conn.execute(
                "UPDATE hawk_devices SET revoked_at=now(), token=NULL, token_hash=NULL "
                "WHERE name=$1 AND revoked_at IS NULL", name[:100])
        # Bekleyen/aktif komutları İPTAL et — ama done/failed GEÇMİŞİ KORU (silme yok).
        try:
            if email:
                await conn.execute(
                    "UPDATE hawk_device_commands SET status='cancelled', updated_at=now() "
                    "WHERE device=$1 AND email=$2 AND status IN ('pending','approved','sent')", name[:100], email)
            else:
                await conn.execute(
                    "UPDATE hawk_device_commands SET status='cancelled', updated_at=now() "
                    "WHERE device=$1 AND status IN ('pending','approved','sent')", name[:100])
        except Exception:
            pass
    n = 0
    try:
        n = int(str(res).split()[-1])  # asyncpg 'UPDATE <n>'
    except Exception:
        n = 0
    await M.remember_episode("event", f"[device] cihaz REVOKE (soft-delete, geçmiş korundu): {name} (sahip: {email or 'admin'})")
    return {"ok": n > 0, "revoked": n, "deleted": n, "error": (None if n > 0 else "not_found")}


async def set_device_offline(token: str) -> Dict[str, Any]:
    """Daemon temiz kapanınca çağrılır: cihazı ANINDA offline göster (last_seen'i eşiğin gerisine al).
    Böylece PC/santral kapanınca 90sn beklemeden 'çevrimdışı' görünür. Token ile auth."""
    if not token:
        return {"ok": False, "error": "no_token"}
    th = _hash_token(token)
    pool = await _get_pool()
    async with pool.acquire() as conn:
        # 95sn geçmiş → frontend eşiği (90sn) aşılır → anında offline; 'az önce' olarak görünür.
        await conn.execute(
            "UPDATE hawk_devices SET last_seen = now() - interval '95 seconds' "
            "WHERE token_hash=$1 OR token=$2", th, token)
    return {"ok": True}


async def verify_fingerprint(token: str, presented_fp: str) -> bool:
    """Cihaz-donanım fingerprint doğrulaması (kopyalanan credential başka PC'de çalışmasın — B/test 5,21).
    TOFU+lenient: cihazın kayıtlı fp'si YOKSA → presented'ı kaydet + izin ver (eski istemci kilitlenmez).
    Kayıtlı fp VARSA ve presented FARKLI (boş değil) → RED. presented boşsa geriye-uyumlu izin (log).
    Gerçek kopya-saldırısı: saldırgan aynı santralı KENDİ PC'sinde çalıştırır → farklı fp → RED."""
    if not token:
        return False
    th = _hash_token(token)
    fp = (presented_fp or "").strip()[:80]
    pool = await _get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT fingerprint FROM hawk_devices WHERE token_hash=$1 OR token=$2", th, token)
        if not row:
            return True   # geçersiz token zaten auth'ta yakalanır; burada engelleme
        stored = (row["fingerprint"] or "").strip()
        if not stored:
            if fp:
                await conn.execute(
                    "UPDATE hawk_devices SET fingerprint=$1 WHERE token_hash=$2 OR token=$3", fp, th, token)
            return True   # ilk kullanım (kilitle) veya legacy (fp yok)
        if fp and fp != stored:
            return False  # farklı donanım → kopyalanmış credential
        return True


async def _device_by_token(token: str) -> Optional[Dict[str, Any]]:
    """Token'ı HASH ile eşle (legacy düz-metin token'a da geriye-uyumlu). {name,email} döner."""
    if not token:
        return None
    th = _hash_token(token)
    pool = await _get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT name, email, workspace FROM hawk_devices WHERE token_hash=$1 OR token=$2", th, token)
        if row:
            await conn.execute("UPDATE hawk_devices SET last_seen=now() WHERE token_hash=$1 OR token=$2", th, token)
            return {"name": row["name"], "email": row["email"], "workspace": row["workspace"]}
    return None


async def _device_owner(device_name: str) -> Optional[str]:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT email FROM hawk_devices WHERE name=$1", device_name)
        return row["email"] if row else None


async def queue_command(device: str, command: str, kind: str = "shell",
                        email: Optional[str] = None) -> Dict[str, Any]:
    """HAWK komut kuyruklar. Risk kapısı: blocked / pending(ara) / approved.
    email verilirse cihazın SAHİBİ olmalı — başka kullanıcının cihazına komut YASAK (Md.20)."""
    if email is not None:
        # SAHİPLİK: cihaz adı BİRDEN FAZLA hesapta olabilir (ör. iki hesapta da "Bilgisayarım").
        # Bu yüzden İSİM+EMAİL ile kontrol et — yoksa _device_owner isimle yanlış hesabı bulup
        # "bu cihaz size ait değil" der (kullanıcının KENDİ cihazına komut çalışmaz). Md.20.
        pool = await _get_pool()
        async with pool.acquire() as conn:
            owns = await conn.fetchval(
                "SELECT 1 FROM hawk_devices WHERE name=$1 AND email=$2 LIMIT 1", device, email)
            exists = owns or await conn.fetchval(
                "SELECT 1 FROM hawk_devices WHERE name=$1 LIMIT 1", device)
        if not owns:
            if not exists:
                return {"ok": False, "status": "not_found", "reason": "cihaz bulunamadı"}
            await M.remember_episode("event", f"[device] TENANT İHLALİ engellendi: {email} -> {device}")
            return {"ok": False, "status": "forbidden", "reason": "bu cihaz size ait değil"}

    # SANTRAL CLAIM UYUMU (KRİTİK): email=None (owner/admin bypass) ise komutu cihazın SAHİBİ
    # email'iyle ekle. Daemon poll'u cihazın email'iyle claim eder; NULL-email komut ASLA claim
    # edilmez → komut sonsuza dek 'approved'da kalır (computer_use hiç çalışmaz). Bu yüzden sahibi çöz.
    if email is None:
        try:
            email = await _device_owner(device)
        except Exception:
            email = None

    # Dosya-yazımı GÖVDESİ (heredoc/here-string içeriği) inert'tir — komut değil veri.
    # Güvenlik/onay kontrollerini gövdeyi ÇIKARIP yap: içerikte 'ödeme/abonelik/rm/drop' geçse de
    # dosyaya yazmak bu komutu ÇALIŞTIRMAZ (yanlış-pozitif engellenir). Hedef/işlem yine denetlenir.
    check_cmd = _strip_write_body(command)
    reason = block_reason(check_cmd)
    if reason:
        pool = await _get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO hawk_device_commands (device, command, kind, status, risk, email) "
                "VALUES ($1,$2,$3,'blocked','critical',$4)",
                device, command[:2000], kind, email)
        await M.remember_episode("event", f"[device] BLOKLANDI ({reason}): {command[:80]}")
        return {"ok": False, "status": "blocked", "reason": f"yıkıcı komut ({reason}) — asla çalıştırılmaz"}

    if kind == "computer_use":
        # GUI eylemi (fare/klavye/ekran) shell komutu DEĞİL → shell risk-sınıflaması UYGULANMAZ
        # (yoksa JSON payload yanlış 'pending'e düşüp hiç çalışmıyordu). Owner-only + santral kendi
        # hassas-pencere korumasına (banka/şifre) sahip → doğrudan approved.
        status, risk, approval_reason = "approved", "low", ""
    else:
        c = classify(check_cmd, tool="device_shell")
        dev_reason = device_needs_approval(command)      # npm/pip install, dosya silme vb. → onay
        escalate = bool(c["escalate"]) or bool(dev_reason)
        status = "pending" if escalate else "approved"
        risk = c["risk"] if c["escalate"] else ("medium" if dev_reason else "low")
        approval_reason = c["reason"] if c["escalate"] else (dev_reason or "")
    pool = await _get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO hawk_device_commands (device, command, kind, status, risk, email)
               VALUES ($1,$2,$3,$4,$5,$6) RETURNING id""",
            device, command[:2000], kind, status, risk, email)
        cmd_id = int(row["id"])

    # NOT: Riskli komutlar için TELEFON ARAMASI YOK. Onay, kullanıcının kendi Workspace/Cihazlar
    # panelinden (in-app) verilir. Arama özelliği kapalı — her kullanıcının komutu için kimse aranmaz.
    # (Telefon araması yalnız Soner'e özel, ayrı akışta; genel cihaz onayına bağlanmaz.)
    await M.remember_episode("action", f"[device] komut kuyruğa ({status}): {command[:80]} @ {device}")
    return {"ok": True, "command_id": cmd_id, "status": status, "risk": risk,
            "approval_reason": approval_reason}


async def approve_command(cmd_id: int, approve: bool = True, email: Optional[str] = None) -> Dict[str, Any]:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        # email verildiyse (admin değil) → sadece KENDİ cihazının komutunu onaylayabilir
        if email is not None:
            row = await conn.fetchrow("SELECT email FROM hawk_device_commands WHERE id=$1", cmd_id)
            if not row or row["email"] != email:
                return {"ok": False, "error": "forbidden", "command_id": cmd_id}
        await conn.execute(
            "UPDATE hawk_device_commands SET status=$1, updated_at=now() WHERE id=$2 AND status='pending'",
            "approved" if approve else "rejected", cmd_id)
    return {"ok": True, "command_id": cmd_id, "status": "approved" if approve else "rejected"}


async def poll_commands(token: str) -> Dict[str, Any]:
    """Cihaz ajanı çağırır — SADECE approved komutları alır, 'sent' yapar."""
    dev = await _device_by_token(token)
    if not dev:
        return {"ok": False, "error": "invalid_token", "commands": []}
    device = dev["name"]
    dev_email = dev.get("email")
    workspace = dev.get("workspace")
    pool = await _get_pool()
    async with pool.acquire() as conn:
        # ATOMİK CLAIM: komutu SELECT+UPDATE yerine tek UPDATE...RETURNING ile kap.
        # FOR UPDATE SKIP LOCKED → aynı token'la BİRDEN FAZLA daemon çalışsa bile (stray/çoklu)
        # her komut TEK daemon'a gider (çift-çalıştırma/yarış yok). İSİM+EMAİL ile izole.
        rows = await conn.fetch(
            "UPDATE hawk_device_commands SET status='sent', updated_at=now() WHERE id IN ("
            "  SELECT id FROM hawk_device_commands "
            "  WHERE device=$1 AND (email=$2 OR ($2 IS NULL AND email IS NULL)) AND status='approved' "
            "  ORDER BY id LIMIT 5 FOR UPDATE SKIP LOCKED"
            ") RETURNING id, command, kind",
            device, dev_email)
        cmds = [dict(r) for r in rows]
    return {"ok": True, "device": device, "workspace": workspace, "commands": cmds}


async def submit_result(token: str, cmd_id: int, output: str, ok: bool = True) -> Dict[str, Any]:
    dev = await _device_by_token(token)
    if not dev:
        return {"ok": False, "error": "invalid_token"}
    device = dev["name"]
    dev_email = dev.get("email")
    pool = await _get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE hawk_device_commands SET status=$1, output=$2, updated_at=now() "
            "WHERE id=$3 AND device=$4 AND (email=$5 OR ($5 IS NULL AND email IS NULL))",
            "done" if ok else "failed", str(output)[:8000], cmd_id, device, dev_email)
    await M.remember_episode("observation", f"[device] sonuç ({'ok' if ok else 'fail'}) cmd#{cmd_id} @ {device}: {str(output)[:120]}")
    return {"ok": True}
