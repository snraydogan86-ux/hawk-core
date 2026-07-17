"""HAWK CORE — Planner (Planning Engine). Hedefi uygulanabilir adımlara böler."""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List

from .engine import brain_chat


def _extract_json(text: str) -> Any:
    """LLM çıktısından JSON çıkar (```json blokları dahil)."""
    if not text:
        return None
    m = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
    raw = m.group(1) if m else text
    # ilk [ veya { ile son ] veya } arası
    s = raw.find("["); e = raw.rfind("]")
    if s == -1:
        s = raw.find("{"); e = raw.rfind("}")
    if s != -1 and e != -1:
        raw = raw[s:e + 1]
    try:
        return json.loads(raw)
    except Exception:
        return None


async def plan_goal(title: str, description: str = "", tools: List[str] | None = None) -> List[Dict[str, Any]]:
    """Hedefi 2-6 somut adıma böler. Döner: [{"title","detail","tool"}]"""
    tools_hint = ""
    if tools:
        tools_hint = ("\nKullanılabilir araçlar (uygunsa 'tool' alanına TAM adını yaz, "
                      f"yoksa boş bırak): {', '.join(tools)}")
    prompt = (
        "Sen HAWK'ın Planlama Motorusun. Verilen hedefi uygulanabilir, somut adımlara böl. "
        "SADECE JSON dizi döndür, başka hiçbir şey yazma. Her adım: "
        '{"title": "...", "detail": "...", "tool": ""}. En fazla 6 adım, mantıklı sırada.'
        f"{tools_hint}\n\n"
        f"HEDEF: {title}\nAÇIKLAMA: {description}\n\nJSON:"
    )
    r = await brain_chat([{"role": "user", "content": prompt}], temperature=0.3, max_tokens=700)
    data = _extract_json(r.get("text", "")) if r.get("ok") else None
    steps: List[Dict[str, Any]] = []
    if isinstance(data, list):
        for it in data[:6]:
            if isinstance(it, dict) and it.get("title"):
                steps.append({
                    "title": str(it.get("title"))[:500],
                    "detail": str(it.get("detail") or "")[:2000],
                    "tool": str(it.get("tool") or "")[:100],
                })
    if not steps:
        # Plan üretilemezse hedefi tek görev yap
        steps = [{"title": title, "detail": description, "tool": ""}]
    return steps
