"""
HAWK CORE — Orkestratör.

Akış (senin mimarin):
  Goal → Planner → Task Queue → Tool Manager(Tool Selection) → çalıştır
       → Self Reflection/Critique → Episodic Memory → ilerleme güncelle
think_once(): otonom döngü için tek adım (Scheduler/Background bunu çağırır).
"""
from __future__ import annotations

from typing import Any, Dict, List

from . import agents as A
from . import goals as G
from . import memory as M
from . import reflection as R
from . import tools as T
from .engine import engine_info
from .planner import plan_goal


async def run_goal(title: str, description: str = "", *, max_steps: int = 6) -> Dict[str, Any]:
    """Bir hedefi uçtan uca yürüt: planla → görevleri çalıştır → eleştir → özetle."""
    goal_id = await G.create_goal(title, description)
    await M.remember_episode("event", f"Yeni hedef: {title}", goal_id=goal_id)

    tool_names = [t["name"] for t in T.list_tools()]
    steps = await plan_goal(title, description, tools=tool_names)
    for s in steps[:max_steps]:
        await G.add_task(s["title"], s.get("detail", ""), goal_id=goal_id, tool=s.get("tool", ""))

    results: List[Dict[str, Any]] = []
    total = 0
    done = 0
    while True:
        task = await G.next_pending(goal_id=goal_id)
        if not task:
            break
        total += 1
        await G.update_task(task["id"], status="running", inc_attempts=True)
        await M.remember_episode("action", f"Görev başladı: {task['title']}",
                                 goal_id=goal_id, task_id=task["id"])
        exe = await A.dispatch(task["title"], task.get("detail", ""), task.get("tool", ""))
        status = "done" if exe.get("ok") else "failed"
        await G.update_task(task["id"], status=status, result=exe.get("result", ""),
                            tool=exe.get("tool", ""))
        ref = await R.reflect(task["title"], exe.get("result", ""),
                              goal_id=goal_id, task_id=task["id"])
        await M.remember_episode("observation",
                                 f"Görev {status}: {task['title']} → {exe.get('result','')[:200]}",
                                 goal_id=goal_id, task_id=task["id"])
        if exe.get("ok"):
            done += 1
        results.append({"task": task["title"], "status": status, "tool": exe.get("tool", ""),
                        "result": exe.get("result", "")[:500], "score": ref.get("score")})
        # ilerleme
        await G.update_goal(goal_id, progress=int(done / max(total, 1) * 100))

    final_status = "done" if done == total and total > 0 else ("failed" if done == 0 else "active")
    await G.update_goal(goal_id, status=final_status,
                        progress=int(done / max(total, 1) * 100) if total else 0)
    await M.remember_episode("event", f"Hedef {final_status}: {title} ({done}/{total})", goal_id=goal_id)
    return {"ok": True, "goal_id": goal_id, "title": title, "status": final_status,
            "steps": len(steps), "done": done, "total": total, "results": results,
            "engine": engine_info()["engine"]}


async def think_once() -> Dict[str, Any]:
    """Otonom tek adım: bekleyen herhangi bir görevi al, çalıştır, eleştir."""
    task = await G.next_pending()
    if not task:
        return {"ok": True, "idle": True, "message": "Bekleyen görev yok"}
    await G.update_task(task["id"], status="running", inc_attempts=True)
    exe = await A.dispatch(task["title"], task.get("detail", ""), task.get("tool", ""))
    status = "done" if exe.get("ok") else "failed"
    await G.update_task(task["id"], status=status, result=exe.get("result", ""), tool=exe.get("tool", ""))
    ref = await R.reflect(task["title"], exe.get("result", ""),
                          goal_id=task.get("goal_id"), task_id=task["id"])
    await M.remember_episode("observation", f"[otonom] {task['title']} → {status} (ajan: {exe.get('agent','')})",
                             goal_id=task.get("goal_id"), task_id=task["id"])
    return {"ok": True, "idle": False, "task": task["title"], "status": status,
            "agent": exe.get("agent", ""), "tool": exe.get("tool", ""),
            "result": exe.get("result", "")[:500], "score": ref.get("score")}


async def core_status() -> Dict[str, Any]:
    return {
        "ok": True,
        "engine": engine_info(),
        "active_goals": await G.active_goals(limit=10),
        "pending_tasks": await G.pending_count(),
        "recent_lessons": await R.recent_lessons(limit=5),
        "recent_memory": await M.recall_episodes(limit=8),
    }
