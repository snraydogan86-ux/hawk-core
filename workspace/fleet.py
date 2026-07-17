"""
HAWK CORE — Fleet (filo) motoru.

Bir hedefi ONLARCA paralel alt-göreve böler ve filo halinde eşzamanlı çalıştırır.
"Milyonlarca ajan" birebir değil (tek GPU endpoint + bütçe sınırı) — ama gerçek,
ölçeklenebilir paralel ajan yürütme. Riskli adımlar policy kapısından geçer.

GÜVENLİK: meşru işler. Spam/sahte hesap/ToS ihlali YOK (policy + dangerous blocks).
"""
from __future__ import annotations

import asyncio
import os
from typing import Any, Dict, List

from . import agents as A
from . import memory as M
from .engine import brain_chat, engine_info
from .planner import _extract_json

# Gerçekçi eşzamanlılık tavanı (tek endpoint'i boğmamak + maliyet)
FLEET_MAX = int(os.getenv("HAWK_FLEET_MAX", "24"))


async def decompose(objective: str, fanout: int = 8) -> List[Dict[str, str]]:
    """Geniş bir hedefi N bağımsız, paralel çalışabilir alt-göreve böl."""
    fanout = max(1, min(fanout, FLEET_MAX * 4))
    prompt = (
        f"Şu hedefi {fanout} adet BAĞIMSIZ, paralel çalışabilir alt-göreve böl. "
        "Her biri tek başına yürütülebilir olmalı. SADECE JSON dizi: "
        '[{"title":"...","detail":"..."}].\n\n'
        f"HEDEF: {objective}\n\nJSON:"
    )
    r = await brain_chat([{"role": "user", "content": prompt}], temperature=0.4, max_tokens=1500)
    data = _extract_json(r.get("text", "")) if r.get("ok") else None
    tasks: List[Dict[str, str]] = []
    if isinstance(data, list):
        for it in data[:fanout]:
            if isinstance(it, dict) and it.get("title"):
                tasks.append({"title": str(it["title"])[:300], "detail": str(it.get("detail") or "")[:1500]})
    if not tasks:
        tasks = [{"title": objective, "detail": ""}]
    return tasks


async def run_objective(objective: str, fanout: int = 8, concurrency: int | None = None) -> Dict[str, Any]:
    """Hedefi parçala → filo halinde eşzamanlı yürüt → sonuçları birleştir."""
    conc = min(concurrency or FLEET_MAX, FLEET_MAX)
    tasks = await decompose(objective, fanout=fanout)
    await M.remember_episode("event", f"[fleet] hedef: {objective[:120]} → {len(tasks)} paralel görev (eşzamanlı={conc})")

    sem = asyncio.Semaphore(conc)
    results: List[Dict[str, Any]] = []

    async def _one(idx: int, t: Dict[str, str]):
        async with sem:
            try:
                exe = await A.dispatch(t["title"], t.get("detail", ""))
                results.append({"i": idx, "task": t["title"], "ok": exe.get("ok"),
                                "agent": exe.get("agent", ""), "tool": exe.get("tool", ""),
                                "escalated": exe.get("escalated", False),
                                "result": str(exe.get("result", ""))[:600]})
            except Exception as e:  # noqa: BLE001
                results.append({"i": idx, "task": t["title"], "ok": False, "result": f"hata: {e}"[:300]})

    await asyncio.gather(*[_one(i, t) for i, t in enumerate(tasks)])
    results.sort(key=lambda x: x["i"])
    done = sum(1 for r in results if r.get("ok"))
    escalated = sum(1 for r in results if r.get("escalated"))
    await M.remember_episode("observation",
                             f"[fleet] tamam: {done}/{len(results)} başarılı, {escalated} kritik→Soner arandı")
    return {"ok": True, "objective": objective, "engine": engine_info()["engine"],
            "fanout": len(tasks), "concurrency": conc, "done": done,
            "escalated": escalated, "total": len(results), "results": results}


def fleet_info() -> Dict[str, Any]:
    return {"ok": True, "fleet_max_concurrency": FLEET_MAX,
            "engine": engine_info(),
            "note": "Eşzamanlılık tavanı tek GPU endpoint kapasitesi + bütçeyle sınırlı. "
                    "Daha fazla GPU cloud worker = daha yüksek tavan."}
