"""
HAWK CORE — Self-Direction (kendi hedefini üretme).

"Bana ihtiyacınız kalmasın": görev kuyruğu boşalınca HAWK, kalıcı hedeflerine göre
KENDİ görevlerini üretir ve peşinden gider. Kritik adımlar policy kapısından geçer
(Soner aranır). Güvenli + sınırlı: idle'da, aralıklı, az sayıda görev.
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict, List

from . import goals as G
from . import memory as M
from .engine import brain_chat
from .planner import _extract_json

STANDING_KEY = "standing_objectives"
GEN_INTERVAL = int(os.getenv("HAWK_SELFDIRECT_INTERVAL", "1800"))  # saniye
_MAX_NEW = 3
_last_gen = 0.0

# Varsayılan kalıcı hedefler — güvenli, üretken, sürekli değer
_DEFAULT_OBJECTIVES = [
    "Sistem sağlığını ve container'ları izle, sorun varsa raporla",
    "Kod ve güvenlik öz-denetimi yap, riskleri bul",
    "Açık bulguları gözden geçir, kritik olanlar için patch öner (Soner'i ara)",
    "HAWK token görünürlüğü ve büyüme fırsatlarını araştır",
]


async def get_objectives() -> List[str]:
    raw = await M.get_fact(STANDING_KEY)
    if raw:
        items = [x.strip() for x in raw.split("|") if x.strip()]
        if items:
            return items
    return _DEFAULT_OBJECTIVES


async def set_objectives(objectives: List[str]) -> None:
    await M.set_fact(STANDING_KEY, " | ".join(o.strip() for o in objectives if o.strip()))


async def generate(max_new: int = _MAX_NEW) -> Dict[str, Any]:
    """Kalıcı hedeflerden somut, güvenli görevler üret ve kuyruğa ekle."""
    objs = await get_objectives()
    lessons = []
    try:
        from . import reflection as R
        lessons = await R.recent_lessons(limit=5)
    except Exception:
        pass
    prompt = (
        "Sen HAWK'ın kendi-yönelim motorusun. Aşağıdaki KALICI HEDEFLER ve son derslere göre, "
        f"şu an yapılacak {max_new} adet SOMUT, güvenli, tek başına yürütülebilir görev öner. "
        "Tehlikeli/geri-dönülemez/ücretli şeyler önerme. SADECE JSON: "
        '[{"title":"...","detail":"..."}].\n\n'
        f"KALICI HEDEFLER:\n- " + "\n- ".join(objs) + "\n\n"
        f"SON DERSLER:\n- " + "\n- ".join(lessons or ["(yok)"]) + "\n\nJSON:"
    )
    r = await brain_chat([{"role": "user", "content": prompt}], temperature=0.5, max_tokens=600)
    data = _extract_json(r.get("text", "")) if r.get("ok") else None
    added = []
    if isinstance(data, list):
        for it in data[:max_new]:
            if isinstance(it, dict) and it.get("title"):
                tid = await G.add_task(str(it["title"])[:300], str(it.get("detail") or "")[:1500], priority=6)
                added.append({"id": tid, "title": it["title"]})
    await M.remember_episode("thought", f"[self-direct] {len(added)} yeni görev üretildi (kendi hedeflerinden)")
    return {"ok": True, "added": added}


async def maybe_generate() -> Dict[str, Any]:
    """Idle'da, aralıklı olarak kendi görevini üret (rate-limited)."""
    global _last_gen
    if str(os.getenv("HAWK_SELFDIRECT", "")).strip() not in ("1", "true", "yes", "on"):
        return {"ok": True, "skipped": "disabled"}
    now = time.time()
    if now - _last_gen < GEN_INTERVAL:
        return {"ok": True, "skipped": "interval"}
    # kuyrukta iş varsa üretme
    if await G.pending_count() > 0:
        return {"ok": True, "skipped": "busy"}
    _last_gen = now
    return await generate()
