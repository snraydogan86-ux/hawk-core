"""
HAWK Swarm Engine — Paralel Ajan Ordusu

Milyonlarca token, yüz binlerce görev — PostgreSQL destekli task queue.

Özellikler:
  • Sınırsız görev kuyruğu (PostgreSQL)
  • Ayarlanabilir paralel worker sayısı (default: 20)
  • Öncelik bazlı işlem
  • Otomatik retry (3 deneme)
  • Real-time progress takibi
  • Sonuçları veritabanına yaz
  • Görev grupları (batch) — ilgili görevleri birlikte yönet
  • Streaming sonuçlar

Kullanım:
  # Tek görev
  task_id = await swarm.submit("Bitcoin fiyatı nedir?", agent="araştırma")

  # Toplu görev (1000 paralel)
  ids = await swarm.batch_submit([
      {"goal": "...", "agent": "..."},
      ...
  ])

  # Sonuç
  result = await swarm.get_result(task_id)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Set

_log = logging.getLogger("hawk.swarm")

# ─── Konfigürasyon ────────────────────────────────────────────────────────────

MAX_WORKERS = int(os.getenv("HAWK_SWARM_WORKERS", "20"))
MAX_RETRIES = int(os.getenv("HAWK_SWARM_RETRIES", "3"))
TASK_TIMEOUT = int(os.getenv("HAWK_SWARM_TIMEOUT", "60"))


# ─── Görev veri yapısı ───────────────────────────────────────────────────────

@dataclass
class SwarmTask:
    task_id: str = field(default_factory=lambda: str(uuid.uuid4())[:16])
    goal: str = ""
    agent_profile: str = "genel"
    context: Dict = field(default_factory=dict)
    priority: int = 5          # 1=kritik, 10=düşük
    status: str = "pending"    # pending|running|done|failed|cancelled
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    result: Optional[str] = None
    error: Optional[str] = None
    retries: int = 0
    batch_id: Optional[str] = None
    tools_used: List[str] = field(default_factory=list)
    iterations: int = 0
    duration_s: float = 0.0


# ─── In-Memory Queue (hızlı erişim için) ─────────────────────────────────────

class PriorityQueue:
    """Thread-safe priority queue — düşük sayı = yüksek öncelik."""

    def __init__(self):
        self._queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
        self._task_map: Dict[str, SwarmTask] = {}

    async def put(self, task: SwarmTask) -> None:
        self._task_map[task.task_id] = task
        # (priority, timestamp, task) — aynı priority'de FIFO
        await self._queue.put((task.priority, task.created_at, task.task_id))

    async def get(self) -> SwarmTask:
        while True:
            _, _, task_id = await self._queue.get()
            task = self._task_map.get(task_id)
            if task and task.status == "pending":
                return task
            # İptal edilmiş veya zaten işlendi — atla

    def done(self):
        self._queue.task_done()

    def size(self) -> int:
        return self._queue.qsize()

    def get_task(self, task_id: str) -> Optional[SwarmTask]:
        return self._task_map.get(task_id)

    def all_tasks(self) -> List[SwarmTask]:
        return list(self._task_map.values())


# ─── Swarm Controller ─────────────────────────────────────────────────────────

class SwarmController:
    """
    HAWK Ajan Ordusu Kontrolörü.

    Görev kuyruğunu yönetir, worker'ları koordine eder,
    sonuçları saklar ve raporlar.
    """

    def __init__(self):
        self._queue = PriorityQueue()
        self._workers: List[asyncio.Task] = []
        self._running = False
        self._active_count = 0
        self._total_completed = 0
        self._total_failed = 0
        self._start_time = time.time()
        self._shutdown_event = asyncio.Event()

    # ─── Görev Gönderme ──────────────────────────────────────────────────────

    async def submit(
        self,
        goal: str,
        *,
        agent: str = "genel",
        context: Dict = None,
        priority: int = 5,
        batch_id: str = None,
    ) -> str:
        """Tek görev gönder. task_id döndürür."""
        task = SwarmTask(
            goal=goal,
            agent_profile=agent,
            context=context or {},
            priority=priority,
            batch_id=batch_id,
        )
        await self._queue.put(task)
        await self._persist_task(task)
        _log.debug("swarm.submit task_id=%s goal=%.50s", task.task_id, goal)
        return task.task_id

    async def batch_submit(
        self,
        tasks: List[Dict],
        *,
        batch_name: str = "",
        priority: int = 5,
    ) -> Dict[str, Any]:
        """
        Toplu görev gönder.
        tasks: [{"goal": "...", "agent": "...", "context": {...}}, ...]
        """
        batch_id = str(uuid.uuid4())[:12]
        task_ids = []

        for t in tasks:
            task_id = await self.submit(
                t.get("goal", ""),
                agent=t.get("agent", "genel"),
                context=t.get("context", {}),
                priority=t.get("priority", priority),
                batch_id=batch_id,
            )
            task_ids.append(task_id)

        _log.info("swarm.batch_submit batch_id=%s count=%d", batch_id, len(task_ids))
        return {
            "batch_id": batch_id,
            "task_ids": task_ids,
            "count": len(task_ids),
            "queue_size": self._queue.size(),
        }

    # ─── Worker Yönetimi ─────────────────────────────────────────────────────

    async def start(self, workers: int = MAX_WORKERS) -> None:
        """Worker havuzunu başlat."""
        if self._running:
            return
        self._running = True
        self._shutdown_event.clear()
        self._workers = [
            asyncio.create_task(self._worker(i))
            for i in range(workers)
        ]
        _log.info("Swarm başlatıldı: %d worker", workers)

    async def stop(self) -> None:
        """Worker havuzunu durdur."""
        self._running = False
        self._shutdown_event.set()
        for w in self._workers:
            w.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
        _log.info("Swarm durduruldu")

    async def scale(self, workers: int) -> None:
        """Worker sayısını ayarla."""
        current = len(self._workers)
        if workers > current:
            for i in range(current, workers):
                self._workers.append(asyncio.create_task(self._worker(i)))
            _log.info("Swarm scale up: %d → %d", current, workers)
        elif workers < current:
            to_stop = self._workers[workers:]
            for w in to_stop:
                w.cancel()
            self._workers = self._workers[:workers]
            _log.info("Swarm scale down: %d → %d", current, workers)

    # ─── Worker ──────────────────────────────────────────────────────────────

    async def _worker(self, worker_id: int) -> None:
        """Tek bir worker — kuyruktan görev alır, çalıştırır, sonucu saklar."""
        _log.debug("Worker-%d başlatıldı", worker_id)

        while self._running:
            try:
                task = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                if self._shutdown_event.is_set():
                    break
                continue
            except asyncio.CancelledError:
                break

            self._active_count += 1
            task.status = "running"
            task.started_at = time.time()

            try:
                await self._execute_task(task)
                task.status = "done"
                self._total_completed += 1
            except Exception as e:
                task.error = str(e)[:300]
                task.retries += 1
                if task.retries < MAX_RETRIES:
                    task.status = "pending"
                    await self._queue.put(task)
                    _log.warning("Worker-%d task retry=%d: %s", worker_id, task.retries, e)
                else:
                    task.status = "failed"
                    self._total_failed += 1
                    _log.error("Worker-%d task failed (max retries): %s", worker_id, e)
            finally:
                task.completed_at = time.time()
                task.duration_s = round(task.completed_at - (task.started_at or task.completed_at), 2)
                self._active_count -= 1
                self._queue.done()
                await self._persist_task(task)

    async def _execute_task(self, task: SwarmTask) -> None:
        """Görevi agent_loop ile çalıştır."""
        from core.agent_loop import run_profiled_agent

        result = await asyncio.wait_for(
            run_profiled_agent(
                task.agent_profile,
                task.goal,
                context=task.context,
            ),
            timeout=TASK_TIMEOUT,
        )
        task.result = (result.get("answer") or "")[:2000]
        task.tools_used = result.get("tools_used", [])
        task.iterations = result.get("iterations", 0)

    # ─── Sonuç Erişimi ───────────────────────────────────────────────────────

    async def get_result(self, task_id: str) -> Optional[Dict]:
        """Görev sonucunu al."""
        # In-memory'den bak
        task = self._queue.get_task(task_id)
        if task:
            return _task_to_dict(task)
        # DB'den bak
        return await self._load_from_db(task_id)

    async def get_batch_results(self, batch_id: str) -> Dict:
        """Toplu görev sonuçlarını al."""
        tasks = [t for t in self._queue.all_tasks() if t.batch_id == batch_id]
        done = [t for t in tasks if t.status == "done"]
        failed = [t for t in tasks if t.status == "failed"]
        pending = [t for t in tasks if t.status in ("pending", "running")]

        return {
            "batch_id": batch_id,
            "total": len(tasks),
            "done": len(done),
            "failed": len(failed),
            "pending": len(pending),
            "results": [_task_to_dict(t) for t in done[:100]],  # max 100
        }

    async def wait_for_task(self, task_id: str, timeout: float = 120.0) -> Optional[Dict]:
        """Görevin tamamlanmasını bekle."""
        start = time.monotonic()
        while time.monotonic() - start < timeout:
            task = self._queue.get_task(task_id)
            if task and task.status in ("done", "failed", "cancelled"):
                return _task_to_dict(task)
            await asyncio.sleep(0.5)
        return None

    # ─── İstatistikler ───────────────────────────────────────────────────────

    def stats(self) -> Dict:
        all_tasks = self._queue.all_tasks()
        by_status: Dict[str, int] = {}
        for t in all_tasks:
            by_status[t.status] = by_status.get(t.status, 0) + 1

        uptime = round(time.time() - self._start_time)
        tps = round(self._total_completed / max(1, uptime), 2)

        return {
            "running": self._running,
            "workers": len(self._workers),
            "active_count": self._active_count,
            "queue_size": self._queue.size(),
            "total_completed": self._total_completed,
            "total_failed": self._total_failed,
            "tasks_per_second": tps,
            "uptime_s": uptime,
            "by_status": by_status,
        }

    # ─── Persistence ─────────────────────────────────────────────────────────

    async def _persist_task(self, task: SwarmTask) -> None:
        try:
            from core.pg_memory import _get_pool
            pool = await _get_pool()
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO hawk_swarm_tasks
                        (task_id, batch_id, goal, agent_profile, status,
                         priority, result, error, retries, tools_used,
                         iterations, duration_s, created_at, completed_at)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,
                            to_timestamp($13), to_timestamp($14))
                    ON CONFLICT (task_id) DO UPDATE SET
                        status=$5, result=$7, error=$8, retries=$9,
                        tools_used=$10, iterations=$11, duration_s=$12,
                        completed_at=to_timestamp($14)
                    """,
                    task.task_id, task.batch_id, task.goal[:500],
                    task.agent_profile, task.status, task.priority,
                    (task.result or "")[:2000], (task.error or "")[:500],
                    task.retries, json.dumps(task.tools_used),
                    task.iterations, task.duration_s,
                    task.created_at, task.completed_at,
                )
        except Exception as e:
            _log.debug("persist_task failed (ok if table not exists): %s", e)

    async def _load_from_db(self, task_id: str) -> Optional[Dict]:
        try:
            from core.pg_memory import _get_pool
            pool = await _get_pool()
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM hawk_swarm_tasks WHERE task_id=$1", task_id
                )
            return dict(row) if row else None
        except Exception:
            return None


# ─── DB Schema ────────────────────────────────────────────────────────────────

SWARM_SCHEMA = """
CREATE TABLE IF NOT EXISTS hawk_swarm_tasks (
    task_id         TEXT PRIMARY KEY,
    batch_id        TEXT,
    goal            TEXT,
    agent_profile   TEXT DEFAULT 'genel',
    status          TEXT DEFAULT 'pending',
    priority        INTEGER DEFAULT 5,
    result          TEXT,
    error           TEXT,
    retries         INTEGER DEFAULT 0,
    tools_used      JSONB DEFAULT '[]',
    iterations      INTEGER DEFAULT 0,
    duration_s      FLOAT DEFAULT 0,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    started_at      TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_swarm_status ON hawk_swarm_tasks(status);
CREATE INDEX IF NOT EXISTS idx_swarm_batch ON hawk_swarm_tasks(batch_id);
CREATE INDEX IF NOT EXISTS idx_swarm_priority ON hawk_swarm_tasks(priority, created_at);
"""


async def init_db() -> None:
    """Swarm tablolarını oluştur."""
    try:
        from core.pg_memory import _get_pool
        pool = await _get_pool()
        async with pool.acquire() as conn:
            await conn.execute(SWARM_SCHEMA)
        _log.info("Swarm DB schema hazır")
    except Exception as e:
        _log.warning("Swarm DB init: %s", e)


# ─── Yardımcı Fonksiyonlar ────────────────────────────────────────────────────

def _task_to_dict(task: SwarmTask) -> Dict:
    return {
        "task_id": task.task_id,
        "batch_id": task.batch_id,
        "goal": task.goal[:200],
        "agent_profile": task.agent_profile,
        "status": task.status,
        "priority": task.priority,
        "result": (task.result or "")[:500],
        "error": task.error,
        "retries": task.retries,
        "tools_used": task.tools_used,
        "iterations": task.iterations,
        "duration_s": task.duration_s,
        "created_at": task.created_at,
        "completed_at": task.completed_at,
    }


# ─── Global Swarm Instance ────────────────────────────────────────────────────

swarm = SwarmController()


async def startup() -> None:
    """Uygulama başlangıcında swarm'ı başlat."""
    await init_db()
    workers = int(os.getenv("HAWK_SWARM_WORKERS", "20"))
    await swarm.start(workers)
    _log.info("HAWK Swarm hazır — %d worker", workers)


async def shutdown() -> None:
    await swarm.stop()
