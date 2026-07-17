"""
HAWK CORE — Executive Orkestratör + Cross-Review.

Akış:
  1) Executive hedefi alt-görevlere böler (decompose)
  2) Her alt-görev uygun uzman ajana dağıtılır (agents.dispatch)
  3) CROSS-REVIEW (Md.24): her sonucu FARKLI bir ajan denetler
     — hiçbir ajan kendi işini tek başına "tamam" saymaz
  4) Kalite kapısı (quality_gate) toplu karar verir
  5) Executive nihai özet + kalite verdisi döner

pick_reviewer() ve quality_gate() SAF fonksiyonlardır (LLM'siz test edilebilir).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .agents import AGENTS, dispatch, select_agent
from .engine import brain_chat
from .planner import _extract_json

# Her uygulayıcı ajanın işini denetleyecek TERCİHLİ gözden geçiren (executor != reviewer).
_REVIEW_MAP: Dict[str, str] = {
    "kod": "qa",
    "qa": "kod",
    "guvenlik": "kod",
    "uiux": "qa",
    "arastirma": "genel",
    "yatirimci": "icerik",
    "icerik": "uiux",
    "maliyet": "altyapi",
    "altyapi": "maliyet",
    "token": "arastirma",
    "sunucu": "guvenlik",
    "pazarlama": "icerik",
    "genel": "qa",
    "executive": "guvenlik",
}
# Gözden geçiren bulunamazsa sırayla denenecek yedekler
_REVIEW_FALLBACK = ["qa", "guvenlik", "genel", "kod"]


def pick_reviewer(executor_key: str) -> str:
    """Uygulayıcıyı denetleyecek FARKLI bir ajan seç. ASLA kendini döndürmez (Md.24)."""
    cand = _REVIEW_MAP.get(executor_key)
    if cand and cand != executor_key and cand in AGENTS:
        return cand
    for fb in _REVIEW_FALLBACK:
        if fb != executor_key and fb in AGENTS:
            return fb
    # son çare: kendisi olmayan herhangi bir ajan
    for k in AGENTS:
        if k != executor_key:
            return k
    return executor_key  # tek ajan varsa (teorik)


def quality_gate(reviews: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Toplu kalite kararı (SAF).
    Kural: her alt-görev denetlenmiş OLMALI; denetlenmemiş veya reddedilmiş varsa geçmez.
    """
    if not reviews:
        return {"approved": False, "reason": "hiç alt-görev/denetim yok", "passed": 0, "total": 0}
    total = len(reviews)
    unreviewed = [r for r in reviews if not r.get("reviewed")]
    rejected = [r for r in reviews if r.get("reviewed") and not r.get("approved")]
    passed = total - len(unreviewed) - len(rejected)
    approved = not unreviewed and not rejected
    reason = "hepsi denetlendi ve onaylandı" if approved else (
        f"{len(unreviewed)} denetlenmemiş, {len(rejected)} reddedilmiş")
    return {"approved": approved, "reason": reason, "passed": passed, "total": total}


async def _decompose(goal: str, max_subtasks: int = 5) -> List[Dict[str, str]]:
    """Hedefi alt-görevlere böl (LLM). Başarısızsa tek görev döner."""
    prompt = (
        f"Aşağıdaki hedefi en fazla {max_subtasks} bağımsız alt-göreve böl. "
        'SADECE JSON dizisi: [{"task":"...","hint":"kısa"}]. '
        f"HEDEF: {goal}\n\nJSON:"
    )
    try:
        r = await brain_chat([{"role": "user", "content": prompt}], temperature=0.2, max_tokens=400)
        data = _extract_json(r.get("text", "")) if r.get("ok") else None
        if isinstance(data, list) and data:
            out = []
            for d in data[:max_subtasks]:
                if isinstance(d, dict) and d.get("task"):
                    out.append({"task": str(d["task"])[:300], "hint": str(d.get("hint", ""))[:200]})
            if out:
                return out
    except Exception:
        pass
    return [{"task": goal, "hint": ""}]


async def _review(reviewer_key: str, task: str, result_text: str) -> Dict[str, Any]:
    """Gözden geçiren ajan sonucu denetler → {approved, notes}."""
    agent = AGENTS.get(reviewer_key, AGENTS["genel"])
    prompt = (
        f"GÖREV: {task}\n\nUYGULAYICININ SONUCU:\n{result_text[:2000]}\n\n"
        "Bu sonuç görevi doğru/eksiksiz karşılıyor mu? "
        'SADECE JSON: {"approved": true/false, "notes": "kısa gerekçe"}.'
    )
    try:
        r = await brain_chat(
            [{"role": "system", "content": agent["system"]},
             {"role": "user", "content": prompt}],
            temperature=0.0, max_tokens=200)
        data = _extract_json(r.get("text", "")) if r.get("ok") else None
        if isinstance(data, dict):
            return {"approved": bool(data.get("approved")), "notes": str(data.get("notes", ""))[:400]}
    except Exception:
        pass
    return {"approved": False, "notes": "denetim yapılamadı"}


async def orchestrate(goal: str, *, max_subtasks: int = 5) -> Dict[str, Any]:
    """
    Executive orkestrasyon: böl → dağıt → cross-review → kalite kapısı → özet.
    """
    subtasks = await _decompose(goal, max_subtasks)
    results: List[Dict[str, Any]] = []
    reviews: List[Dict[str, Any]] = []

    for st in subtasks:
        task, hint = st["task"], st.get("hint", "")
        exec_res = await dispatch(task, hint)
        executor = exec_res.get("agent", "genel")
        result_text = str(exec_res.get("result", ""))

        reviewer = pick_reviewer(executor)
        rv = await _review(reviewer, task, result_text)
        assert reviewer != executor, "cross-review ihlali: ajan kendini denetleyemez"

        results.append({"task": task, "executor": executor, "result": result_text[:2000],
                        "reviewer": reviewer})
        reviews.append({"task": task, "executor": executor, "reviewer": reviewer,
                        "reviewed": True, "approved": rv["approved"], "notes": rv["notes"]})

    gate = quality_gate(reviews)
    return {
        "ok": True,
        "goal": goal,
        "subtask_count": len(subtasks),
        "results": results,
        "reviews": reviews,
        "quality": gate,
        "verdict": "APPROVED" if gate["approved"] else "NEEDS_WORK",
    }
