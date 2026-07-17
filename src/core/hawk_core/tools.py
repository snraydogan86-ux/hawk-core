"""HAWK CORE — Tool Manager + Tool Selection.

Mevcut tool_engine'in (26 araç: web_search, run_python, run_shell, docker_status,
system_report, ask_expert, ...) üstünde akıllı seçim katmanı.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

from .engine import brain_chat
from .planner import _extract_json


def list_tools() -> List[Dict[str, str]]:
    try:
        from core.tool_engine import _TOOLS
        return [{"name": n, "description": t.get("description", "")[:200]}
                for n, t in _TOOLS.items()]
    except Exception:
        return []


async def select_tool(task_title: str, task_detail: str) -> Dict[str, Any]:
    """Görev için araç + argüman seç. Döner: {"tool": str|"", "args": {...}, "reason": str}"""
    tools = list_tools()
    catalog = "\n".join(f"- {t['name']}: {t['description']}" for t in tools)
    prompt = (
        "Sen HAWK'ın Araç Seçicisisin. Aşağıdaki görev için EN UYGUN aracı seç ve "
        "argümanlarını ver. Araç gerekmiyorsa tool boş bırak (model kendi cevaplar). "
        'SADECE JSON döndür: {"tool": "araç_adı veya boş", "args": {...}, "reason": "kısa"}.\n\n'
        f"ARAÇLAR:\n{catalog}\n\n"
        f"GÖREV: {task_title}\nDETAY: {task_detail}\n\nJSON:"
    )
    r = await brain_chat([{"role": "user", "content": prompt}], temperature=0.1, max_tokens=300)
    data = _extract_json(r.get("text", "")) if r.get("ok") else None
    if isinstance(data, dict):
        return {"tool": str(data.get("tool") or ""), "args": data.get("args") or {},
                "reason": str(data.get("reason") or "")}
    return {"tool": "", "args": {}, "reason": "seçim yapılamadı"}


async def run_tool(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    from core.tool_engine import execute_tool
    return await execute_tool(name, args or {})


async def execute_task(task_title: str, task_detail: str, preferred_tool: str = "") -> Dict[str, Any]:
    """Görevi çalıştır: araç seç → çalıştır; araç yoksa beyin doğrudan cevaplar.
    Döner: {"ok","tool","result"}"""
    sel = {"tool": preferred_tool, "args": {}} if preferred_tool else await select_tool(task_title, task_detail)
    tool = sel.get("tool") or ""
    if tool:
        # argüman yoksa seçiciden tamamla
        if not sel.get("args") and not preferred_tool:
            sel = await select_tool(task_title, task_detail)
            tool = sel.get("tool") or tool
        res = await run_tool(tool, sel.get("args") or {})
        ok = bool(res.get("ok"))
        out = res.get("result") if ok else res.get("error")
        return {"ok": ok, "tool": tool, "result": str(out)[:6000]}
    # Araçsız: beyin doğrudan yürütür
    r = await brain_chat(
        [{"role": "user", "content": f"Görev: {task_title}\n{task_detail}\nBunu yap/cevapla, kısa ve net."}],
        temperature=0.5, max_tokens=400)
    return {"ok": bool(r.get("ok")), "tool": "", "result": r.get("text", "")[:6000]}
