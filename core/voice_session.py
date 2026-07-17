from __future__ import annotations
import time
from typing import Dict, Any
from core.memory_store import recall_fact

_VOICE_STATE: Dict[str, Dict[str, Any]] = {}
def resolve_address(user_id: str = "default_user") -> str:
    try:
        address_pref = (recall_fact("address_preference", user_id=user_id) or "").strip()
    except Exception:
        address_pref = ""

    if address_pref:
        return address_pref

    try:
        user_name = (recall_fact("user_name", user_id=user_id) or "").strip()
    except Exception:
        user_name = ""

    if user_name:
        return user_name

    return ""


def maybe_prefix_address(text: str, user_id: str = "default_user") -> str:
    raw = str(text or "").strip()
    if not raw:
        return raw

    addr = resolve_address(user_id=user_id).strip()
    if not addr:
        return raw

    low = raw.lower()
    if low.startswith(addr.lower() + ",") or low.startswith(addr.lower() + " "):
        return raw

    return f"{addr}, {raw}"


def get_voice_session(session_id: str) -> Dict[str, Any]:
    session_id = str(session_id or "").strip() or "default"
    item = _VOICE_STATE.get(session_id)
    if not item:
        item = {
            "session_id": session_id,
            "mode": "idle",  # idle | listening | thinking | speaking
            "continuous": False,
            "last_user_text": "",
            "last_assistant_text": "",
            "updated_at": time.time(),
        }
        _VOICE_STATE[session_id] = item
    return item

def update_voice_session(session_id: str, **kwargs) -> Dict[str, Any]:
    item = get_voice_session(session_id)
    item.update(kwargs)
    item["updated_at"] = time.time()
    return item

def public_voice_state(session_id: str) -> Dict[str, Any]:
    item = get_voice_session(session_id)
    return {
        "session_id": item["session_id"],
        "mode": item["mode"],
        "continuous": bool(item["continuous"]),
        "last_user_text": item["last_user_text"],
        "last_assistant_text": item["last_assistant_text"],
        "updated_at": item["updated_at"],
    }