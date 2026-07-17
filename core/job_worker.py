"""
FAZ 2 — Kalıcı iş worker'ı / dispatcher.

Servis yeniden başlasa bile görevler kaybolmaz:
  - Worker: claim_next_global (atomik lease, SKIP LOCKED → çift-çalışma yok) → handler → complete/fail.
  - Heartbeat: çalışırken lease yenilenir; worker ölürse lease dolar.
  - Reaper: periyodik sweep_orphans → sahipsiz (lease dolmuş) görev pending'e döner (retry) veya DLQ.
  - Checkpoint: handler ara-durumu kaydeder; yeniden başlatmada kaldığı yerden devam.
  - DLQ: max_retry aşımı → dead_letter (kör tekrar yok).
  - Kill-switch açıkken YENİ görev alınmaz.

Handler imzası: async def h(task: dict, checkpoint: dict) -> dict  (döner {"summary": "..."}).
Handler istediğinde: await save_checkpoint(task_id, state), await append_event(task_id, type).
"""
from __future__ import annotations
import asyncio
import os
import socket

_HANDLERS: dict = {}
WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"
LEASE_S = int(os.getenv("HAWK_JOB_LEASE_S", "60"))
POLL_S = float(os.getenv("HAWK_JOB_POLL_S", "3"))
HEARTBEAT_S = max(8, LEASE_S // 3)
REAP_S = int(os.getenv("HAWK_JOB_REAP_S", "30"))
MAX_TASK_S = int(os.getenv("HAWK_JOB_MAX_TASK_S", "600"))   # görev başına wall-clock sınırı


def register_handler(kind: str, fn):
    _HANDLERS[kind] = fn


def handlers() -> list:
    return sorted(_HANDLERS.keys())


# kind → granüler kill scope (o scope kapalıysa o tür görev alınmaz)
KIND_SCOPE = {"orchestrate": "multi_agent", "research": "research", "echo": ""}


async def _run_one(task: dict):
    from core.agent_orchestration.store import AgentTaskStore as S
    tid = task["task_id"]
    kind = task.get("kind") or ""
    handler = _HANDLERS.get(kind)
    if handler is None:
        await S.fail_task(tid, WORKER_ID, error_code="no_handler", error_summary=f"kind={kind}")
        await S.append_event(tid, "failed", public_message=f"handler yok: {kind}")
        return
    await S.append_event(tid, "started", public_message=f"worker={WORKER_ID} kind={kind}")
    stop = asyncio.Event()

    async def _hb():
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=HEARTBEAT_S)
            except asyncio.TimeoutError:
                try:
                    await S.heartbeat(tid, WORKER_ID, lease_s=LEASE_S)
                except Exception:
                    pass

    hb = asyncio.create_task(_hb())
    try:
        ckpt = await S.load_checkpoint(tid)
        # görev-süresi (wall-clock) sınırı — takılan handler görevi bloklamasın
        res = await asyncio.wait_for(handler(task, ckpt), timeout=MAX_TASK_S)
        summary = str((res or {}).get("summary", "")) if isinstance(res, dict) else str(res or "")
        await S.complete_task(tid, WORKER_ID, result_summary=summary[:400])
        await S.append_event(tid, "completed", public_message=summary[:120])
    except asyncio.CancelledError:
        raise
    except Exception as e:
        d = await S.fail_task(tid, WORKER_ID, error_code="handler_error", error_summary=str(e)[:400])
        await S.append_event(tid, "failed", public_message=str(e)[:120], meta={"status": d.get("status")})
    finally:
        stop.set()
        try:
            await hb
        except Exception:
            pass


async def worker_loop():
    from core.agent_orchestration.store import AgentTaskStore as S
    from core import cost_guard
    print(f"[HAWK][jobq] worker başladı: {WORKER_ID} (lease {LEASE_S}s, poll {POLL_S}s)", flush=True)
    from core import ops_monitor as _ops
    try:
        await S.register_worker(WORKER_ID, kind="vps", capabilities=list(_HANDLERS.keys()),
                                meta={"lease_s": LEASE_S})
    except Exception:
        pass
    _hb_n = 0
    while True:
        try:
            _ops.heartbeat(WORKER_ID, capabilities=list(_HANDLERS.keys()),
                           meta={"lease_s": LEASE_S})
            _hb_n += 1
            if _hb_n % 5 == 1:                       # ~her 5 turda DB heartbeat (yük az)
                try: await S.worker_heartbeat(WORKER_ID)
                except Exception: pass
            if cost_guard.is_killed():           # global kill → hiç görev alma
                await asyncio.sleep(POLL_S * 2)
                continue
            if _ops.budget_blocked():            # günlük bütçe aşıldı → yeni iş başlamaz
                await asyncio.sleep(POLL_S * 2)
                continue
            # granüler kill: scope'u kapalı kind'leri çıkar (yalnız kayıtlı + açık kind'ler)
            allowed = [k for k in _HANDLERS if not cost_guard.is_killed(KIND_SCOPE.get(k, ""))]
            if not allowed:
                await asyncio.sleep(POLL_S * 2)
                continue
            task = await S.claim_next_global(WORKER_ID, lease_s=LEASE_S, kinds=allowed)
            if task is None:
                await asyncio.sleep(POLL_S)
                continue
            await _run_one(task)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[HAWK][jobq] worker hata: {str(e)[:120]}", flush=True)
            await asyncio.sleep(POLL_S)


async def reaper_loop():
    from core.agent_orchestration.store import AgentTaskStore as S
    while True:
        try:
            await asyncio.sleep(REAP_S)
            recovered = await S.sweep_orphans()
            if recovered:
                dl = [r for r in recovered if r.get("status") == "dead_letter"]
                print(f"[HAWK][jobq] orphan sweep: {len(recovered)} kurtarıldı, {len(dl)} DLQ", flush=True)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass


# ── Yerleşik handler'lar ─────────────────────────────────────────────────────
async def _h_echo(task: dict, ckpt: dict) -> dict:
    """Test/demo handler: payload'ı yankılar; checkpoint'li iki-aşama (resume kanıtı)."""
    from core.agent_orchestration.store import AgentTaskStore as S
    tid = task["task_id"]
    payload = task.get("payload") or {}
    step = int(ckpt.get("step", 0))
    if step < 1:
        await S.save_checkpoint(tid, {"step": 1, "seen": payload.get("text", "")})
        await S.append_event(tid, "checkpoint", public_message="step=1")
    if payload.get("fail"):
        raise RuntimeError(payload.get("fail_msg", "kasıtlı hata"))
    return {"summary": f"echo:{payload.get('text', '')}", "step": 2}


async def _h_orchestrate(task: dict, ckpt: dict) -> dict:
    """Multi-agent orkestrasyonu kalıcı kuyruktan çalıştırır (crash-recovery ile)."""
    from core.agent_orchestration import live_runner as LR
    payload = task.get("payload") or {}
    goal = payload.get("goal") or task.get("objective") or ""
    user_scope = payload.get("user_scope") or "system"
    res = await LR.run_orchestration(goal=goal, user_scope=user_scope)
    return {"summary": (res.get("final") or "")[:300]}


def register_defaults():
    register_handler("echo", _h_echo)
    register_handler("orchestrate", _h_orchestrate)
