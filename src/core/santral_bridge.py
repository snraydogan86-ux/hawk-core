"""
HAWK Chat → Santral köprüsü (Md.4/37).

Kullanıcı HAWK chat'ine "santral: docker ps" gibi yazınca, komut KENDİ cihazına
(multi-tenant) risk-gate'ten geçirilerek kuyruklanır; daemon çalıştırır, sonuç döner.

Güvenlik:
  - AÇIK tetikleyici (prefix) gerekir — normal sohbet yanlışlıkla shell'e gitmez.
  - Yalnız kullanıcının KENDİ cihazı (email izolasyonu).
  - Mevcut risk-gate: yıkıcı → blocked; riskli → pending (onay); güvenli → approved.
  - Kayıt hawk_device_commands (audit).

detect_santral() SAF ve test edilebilir.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional, Tuple

# Chat'te santral komutunu tetikleyen açık önekler
_PREFIXES = ["santral:", "çalıştır:", "calistir:", "komut:", "run:", "shell:", "terminal:", "cmd:"]


def detect_santral(message: str) -> Tuple[bool, str]:
    """Mesaj bir santral komutu mu? (is_santral, komut). SAF."""
    m = (message or "").strip()
    low = m.lower()
    for p in _PREFIXES:
        if low.startswith(p):
            return True, m[len(p):].strip()
    return False, ""


async def _pick_device(email: str) -> Optional[str]:
    """Kullanıcının EN SON aktif cihazı (multi-tenant)."""
    from core.hawk_core.devices import list_devices
    devs = await list_devices(email=email)
    if not devs:
        return None
    # last_seen'e göre en taze (list_devices id DESC döner; last_seen bazlı seç)
    devs_sorted = sorted(devs, key=lambda d: (d.get("last_seen") or d.get("created_at") or ""), reverse=True)
    return devs_sorted[0]["name"]


async def exec_from_chat(email: str, message: str, *, device: Optional[str] = None) -> Dict[str, Any]:
    """
    Chat mesajından santral komutu çalıştır (kuyrukla). GÖNDERMEZ-çalıştırmaz; daemon çeker.
    Döner: {ok, santral, status, command_id?, risk?, response}
    """
    is_s, command = detect_santral(message)
    if not is_s:
        return {"ok": False, "santral": False, "response": "Santral komutu değil."}
    if not command:
        return {"ok": False, "santral": True, "response": "Boş komut. Örn: 'santral: docker ps'"}
    if not email:
        return {"ok": False, "santral": True, "response": "Santral için giriş yapmalısın."}

    dev = device or await _pick_device(email)
    if not dev:
        return {"ok": False, "santral": True, "status": "no_device",
                "response": "Bağlı cihazın yok. Önce cihazını kaydet (Ayarlar → Cihazlar) ve "
                            "HAWK Remote Agent'ı çalıştır."}

    from core.hawk_core.devices import queue_command
    res = await queue_command(dev, command, kind="shell", email=email)
    status = res.get("status")

    if status == "blocked":
        msg = f"🚫 Komut engellendi (yıkıcı): {res.get('reason','')}"
    elif status == "forbidden":
        msg = "🚫 Bu cihaz sana ait değil."
    elif status == "pending":
        msg = f"⏳ Riskli komut — onay bekliyor. Onaylayınca '{dev}' cihazında çalışacak."
    elif status == "approved":
        msg = f"✅ Komut '{dev}' cihazına gönderildi (kuyrukta). Sonuç birazdan döner."
    else:
        msg = f"Komut durumu: {status}"

    return {"ok": bool(res.get("ok")), "santral": True, "device": dev,
            "command": command, "status": status, "command_id": res.get("command_id"),
            "risk": res.get("risk"), "response": msg}
