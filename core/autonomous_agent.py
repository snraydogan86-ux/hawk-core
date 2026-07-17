"""
HAWK Autonomous Agent OS — Think → Plan → Execute → Monitor → Improve.

Runs as a background task inside the API container.
Interval: every 10 minutes by default (HAWK_AGENT_INTERVAL_SECONDS).

Capabilities:
  - Detect failures and repair automatically
  - Create and execute tasks
  - Monitor platform health
  - Report results to Soner
  - Self-improve (safe, non-breaking changes only)
  - Email monitoring and classification
  - Security scanning
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

_log = logging.getLogger("hawk.autonomous")

AGENT_INTERVAL = int(os.getenv("HAWK_AGENT_INTERVAL_SECONDS", "600"))  # 10 minutes


@dataclass
class AgentThought:
    ts: float = field(default_factory=time.time)
    phase: str = ""  # think|plan|execute|monitor|reflect
    content: str = ""
    result: Optional[str] = None
    success: bool = True


@dataclass
class AutonomousTask:
    task_id: str = ""
    title: str = ""
    description: str = ""
    priority: int = 5  # 1=highest, 10=lowest
    status: str = "pending"  # pending|running|done|failed
    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    result: Optional[str] = None


_thoughts: List[AgentThought] = []
_tasks: List[AutonomousTask] = []
_running = False
_last_run: float = 0.0
_MAX_HISTORY = 200


class AutonomousHAWK:
    """
    The autonomous HAWK agent. Runs in background, continuously improving the platform.
    """

    @staticmethod
    def add_thought(phase: str, content: str, result: str = None, success: bool = True) -> AgentThought:
        t = AgentThought(phase=phase, content=content, result=result, success=success)
        _thoughts.append(t)
        if len(_thoughts) > _MAX_HISTORY:
            _thoughts.pop(0)
        _log.info(f"[HAWK/{phase.upper()}] {content[:100]}")
        return t

    # ─── THINK phase ─────────────────────────────────────────────────────────

    @staticmethod
    async def think() -> Dict[str, Any]:
        """Analyze current state and identify what needs attention."""
        AutonomousHAWK.add_thought("think", "Sistem durumu analiz ediliyor...")

        issues = []
        opportunities = []

        # Check mail intelligence
        try:
            from core.mail_intelligence import fetch_and_analyze_inbox
            if os.getenv("HAWK_IMAP_HOST"):
                issues.append("email_check")
        except Exception:
            pass

        # Check security events
        try:
            from routers.security_agent import _events
            recent_critical = [e for e in _events if e.severity >= 3 and time.time() - e.ts < 3600]
            if recent_critical:
                issues.append(f"security_critical:{len(recent_critical)}_events")
        except Exception:
            pass

        # Check self-healer findings
        try:
            from core.self_healer import SelfHealer
            if SelfHealer._error_counts:
                top_errors = sorted(SelfHealer._error_counts.items(), key=lambda x: x[1], reverse=True)[:3]
                for cat, cnt in top_errors:
                    if cnt > 5:
                        issues.append(f"recurring_error:{cat}:{cnt}")
        except Exception:
            pass

        state = {
            "issues": issues,
            "opportunities": opportunities,
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        }
        AutonomousHAWK.add_thought("think", f"Analiz tamamlandı: {len(issues)} sorun, {len(opportunities)} fırsat", str(state))
        return state

    # ─── PLAN phase ──────────────────────────────────────────────────────────

    @staticmethod
    async def plan(state: Dict[str, Any]) -> List[AutonomousTask]:
        """Create execution plan based on current state."""
        AutonomousHAWK.add_thought("plan", "Görev planı oluşturuluyor...")
        tasks = []
        import secrets

        for issue in state.get("issues", []):
            if issue == "email_check":
                tasks.append(AutonomousTask(
                    task_id=secrets.token_hex(8),
                    title="Email İstihbarat Taraması",
                    description="Gelen kutusunu tara, önemli mailleri sınıflandır, taslaklar hazırla",
                    priority=3,
                ))
            elif issue.startswith("security_critical"):
                tasks.append(AutonomousTask(
                    task_id=secrets.token_hex(8),
                    title="Güvenlik Müdahalesi",
                    description=f"Kritik güvenlik olayları tespit edildi: {issue}",
                    priority=1,
                ))
            elif issue.startswith("recurring_error"):
                tasks.append(AutonomousTask(
                    task_id=secrets.token_hex(8),
                    title="Hata Onarımı",
                    description=f"Tekrarlayan hata düzeltiliyor: {issue}",
                    priority=2,
                ))

        _tasks.extend(tasks)
        if len(_tasks) > _MAX_HISTORY:
            _tasks[:] = _tasks[-_MAX_HISTORY:]

        AutonomousHAWK.add_thought("plan", f"{len(tasks)} görev planlandı")
        return tasks

    # ─── EXECUTE phase ───────────────────────────────────────────────────────

    @staticmethod
    async def execute(tasks: List[AutonomousTask]) -> List[Dict[str, Any]]:
        """Execute planned tasks — agent loop ile."""
        results = []

        for task in tasks:
            AutonomousHAWK.add_thought("execute", f"Görev yürütülüyor: {task.title}")
            task.status = "running"

            try:
                # Önce özel handler'lar, sonra agent loop
                if "Email" in task.title:
                    result = await AutonomousHAWK._run_email_check()
                elif "Güvenlik" in task.title:
                    result = await AutonomousHAWK._run_security_check()
                elif "Hata Onarımı" in task.title:
                    result = await AutonomousHAWK._run_self_heal()
                else:
                    # Genel görevler için agent loop
                    try:
                        from core.agent_loop import run_profiled_agent
                        agent_result = await asyncio.wait_for(
                            run_profiled_agent("genel", task.description),
                            timeout=60.0,
                        )
                        result = {"agent_answer": agent_result.get("answer", "")[:300],
                                  "tools_used": agent_result.get("tools_used", [])}
                    except Exception:
                        result = {"status": "skipped", "reason": "unknown_task_type"}

                task.status = "done"
                task.completed_at = time.time()
                task.result = json.dumps(result)[:500]
                results.append({"task": task.title, "result": result, "success": True})
                AutonomousHAWK.add_thought("execute", f"Tamamlandı: {task.title}", str(result)[:100])
            except Exception as e:
                task.status = "failed"
                task.completed_at = time.time()
                task.result = str(e)[:200]
                results.append({"task": task.title, "error": str(e)[:100], "success": False})
                AutonomousHAWK.add_thought("execute", f"Başarısız: {task.title}: {e}", success=False)

        return results

    # ─── MONITOR phase ───────────────────────────────────────────────────────

    @staticmethod
    async def monitor() -> Dict[str, Any]:
        """Monitor platform health after execution."""
        AutonomousHAWK.add_thought("monitor", "Platform sağlık kontrolü yapılıyor...")
        try:
            from routers.security_agent import run_full_scan
            results = await run_full_scan()
            overall_ok = all(
                v.get("ok", True) if isinstance(v, dict) else True
                for k, v in results.items() if k != "scanned_at"
            )
            AutonomousHAWK.add_thought("monitor", f"Sağlık kontrolü: {'OK' if overall_ok else 'SORUNLAR VAR'}", str(results)[:200])
            return {"ok": overall_ok, "details": results}
        except Exception as e:
            AutonomousHAWK.add_thought("monitor", f"Monitor hatası: {e}", success=False)
            return {"ok": False, "error": str(e)[:100]}

    # ─── REFLECT phase ───────────────────────────────────────────────────────

    @staticmethod
    async def reflect(state: Dict, results: List, monitor: Dict) -> str:
        """Generate a brief summary of this cycle."""
        done = sum(1 for r in results if r.get("success"))
        failed = len(results) - done
        issues_count = len(state.get("issues", []))
        health = "✅ Sağlıklı" if monitor.get("ok") else "⚠️ Sorunlar var"

        summary = (
            f"HAWK Otonom Döngü Raporu — {datetime.now(tz=timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n"
            f"Tespit edilen sorun: {issues_count}\n"
            f"Yürütülen görev: {len(results)} ({done} başarılı, {failed} başarısız)\n"
            f"Platform durumu: {health}"
        )
        AutonomousHAWK.add_thought("reflect", summary)
        return summary

    # ─── Task implementations ────────────────────────────────────────────────

    @staticmethod
    async def _run_email_check() -> Dict[str, Any]:
        try:
            from core.mail_intelligence import fetch_and_analyze_inbox, save_drafts_to_db
            messages = await asyncio.to_thread(fetch_and_analyze_inbox, max_messages=10)
            if messages:
                try:
                    from routers.deps import get_pool
                    pool = await get_pool()
                    await save_drafts_to_db(messages, pool)
                except Exception:
                    pass
                urgent = [m for m in messages if m.priority in ("critical", "high")]
                if urgent:
                    from core.communication_hub import notify_soner
                    msg = f"📧 {len(urgent)} acil email tespit edildi:\n"
                    for m in urgent[:3]:
                        msg += f"  • [{m.priority.upper()}] {m.subject[:60]} — {m.sender[:40]}\n"
                    await notify_soner(msg, priority="high")
                return {"processed": len(messages), "urgent": len(urgent)}
            return {"processed": 0}
        except Exception as e:
            return {"error": str(e)[:100]}

    @staticmethod
    async def _run_security_check() -> Dict[str, Any]:
        try:
            from routers.security_agent import run_full_scan
            return await run_full_scan()
        except Exception as e:
            return {"error": str(e)[:100]}

    @staticmethod
    async def _run_self_heal() -> Dict[str, Any]:
        try:
            from core.self_healer import SelfHealer
            results = []
            for cat, cnt in list(SelfHealer._error_counts.items()):
                if cnt > 3:
                    result = await SelfHealer.heal_by_category(cat)
                    results.append({"category": cat, "result": result})
            return {"healed": results}
        except Exception as e:
            return {"error": str(e)[:100]}

    # ─── Main loop ───────────────────────────────────────────────────────────

    @classmethod
    async def _recent_user_activity(cls, minutes: int = 15) -> bool:
        """Son N dakikada GERÇEK kullanıcı isteği var mı? (usage_metrics http, iç endpoint'ler hariç)"""
        try:
            from core.pg_memory import _get_pool
            pool = await _get_pool()
            async with pool.acquire() as c:
                row = await c.fetchrow(
                    "SELECT count(*) AS n FROM hawk_usage_metrics WHERE kind='http' "
                    "AND ts > now() - ($1||' minutes')::interval "
                    "AND path NOT LIKE '/api/health%' AND path NOT LIKE '/api/gpu/%' "
                    "AND path NOT LIKE '/api/cost/%' AND path NOT LIKE '/api/doctor/%' "
                    "AND path NOT LIKE '/api/santral/%'",
                    str(minutes))
                return int(row["n"] or 0) > 0
        except Exception:
            return False  # metrik yok/hata → güvenli taraf: çalıştırma (maliyet koruması)

    @classmethod
    async def run_cycle(cls) -> Dict[str, Any]:
        """Run one full Think→Plan→Execute→Monitor→Reflect cycle."""
        global _last_run
        _last_run = time.time()
        start = time.monotonic()

        # MALİYET KORUMASI: gerçek kullanıcı yoksa ücretli beyni ÇAĞIRMA (scale-to-zero kalsın)
        if not await cls._recent_user_activity():
            return {"ok": True, "skipped": "no_recent_user_activity",
                    "summary": "Aktif kullanıcı yok — beyin çağrılmadı (GPU scale-to-zero korundu)"}

        try:
            state = await cls.think()
            tasks = await cls.plan(state)
            results = await cls.execute(tasks)
            monitor = await cls.monitor()
            summary = await cls.reflect(state, results, monitor)
            duration = round(time.monotonic() - start, 2)
            return {
                "ok": True,
                "summary": summary,
                "tasks_done": len(tasks),
                "duration_s": duration,
                "thoughts": len(_thoughts),
            }
        except Exception as e:
            _log.error(f"Autonomous cycle error: {e}")
            return {"ok": False, "error": str(e)[:200]}

    @classmethod
    async def start_background_loop(cls) -> None:
        """Background loop — runs continuously every AGENT_INTERVAL seconds."""
        global _running
        if _running:
            return
        _running = True
        _log.info(f"[HAWK] Autonomous agent started — interval: {AGENT_INTERVAL}s")

        while True:
            try:
                await asyncio.sleep(30)  # First run after 30s startup delay
                while True:
                    result = await cls.run_cycle()
                    _log.info(f"[HAWK] Cycle done: {result.get('summary', '')[:80]}")
                    await asyncio.sleep(AGENT_INTERVAL)
            except asyncio.CancelledError:
                _running = False
                break
            except Exception as e:
                _log.error(f"[HAWK] Autonomous loop error: {e}")
                await asyncio.sleep(60)


# ─── State access ─────────────────────────────────────────────────────────────

def get_thoughts(limit: int = 50) -> List[Dict]:
    return [asdict(t) for t in _thoughts[-limit:]]


def get_tasks(limit: int = 50) -> List[Dict]:
    return [asdict(t) for t in _tasks[-limit:]]


def get_status() -> Dict[str, Any]:
    return {
        "running": _running,
        "last_run": _last_run,
        "last_run_ago_s": round(time.time() - _last_run) if _last_run else None,
        "thoughts_count": len(_thoughts),
        "tasks_count": len(_tasks),
        "interval_s": AGENT_INTERVAL,
    }
