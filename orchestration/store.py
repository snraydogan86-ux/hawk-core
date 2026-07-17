"""
AgentTaskStore — kanonik Task DAG'ın Postgres persistence adaptörü (migration 023).

Gerçek dayanıklılık garantileri:
  - Atomik claim: FOR UPDATE SKIP LOCKED + lease → iki worker AYNI node'u alamaz.
  - Orphan sweep: lease'i dolmuş aktif görev → retry ise pending, değilse failed.
  - Idempotency: idempotency_key UNIQUE → duplicate görev oluşmaz.
  - Optimistic locking: version alanı her update'te artar.
  - Dependency-gated ready: bağımlılığı 'completed' olmayan görev claim edilemez.

Uygulama katmanı (TaskGraph) döngü tespitini yapar; DB self-loop'u ayrıca reddeder.
Ham prompt/secret saklanmaz — yalnız *_ref referansları ve content_hash.
Kimlik düz email değil, user_scope_hash/project_scope_hash olarak taşınır.
"""
from __future__ import annotations

from typing import Any, Optional


async def _pool():
    from core.pg_memory import _get_pool
    return await _get_pool()


class AgentTaskStore:
    # -- oluşturma (idempotent) --
    @staticmethod
    async def create_task(*, task_id: str, user_scope_hash: str, objective: str = "",
                          title: str = "", project_scope_hash: str = "",
                          parent_task_id: Optional[str] = None,
                          root_task_id: Optional[str] = None, priority: int = 5,
                          idempotency_key: Optional[str] = None,
                          max_retry: int = 2) -> dict:
        pool = await _pool()
        async with pool.acquire() as c:
            if idempotency_key:
                existing = await c.fetchrow(
                    "SELECT task_id FROM agent_tasks WHERE idempotency_key=$1", idempotency_key)
                if existing:
                    return {"task_id": existing["task_id"], "created": False}
            row = await c.fetchrow(
                """INSERT INTO agent_tasks
                   (task_id,user_scope_hash,project_scope_hash,parent_task_id,root_task_id,
                    title,objective,priority,idempotency_key,max_retry)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
                   ON CONFLICT (task_id) DO NOTHING
                   RETURNING task_id""",
                task_id, user_scope_hash, project_scope_hash, parent_task_id,
                root_task_id or task_id, title, objective, priority, idempotency_key, max_retry)
            return {"task_id": task_id, "created": row is not None}

    @staticmethod
    async def add_dependency(task_id: str, depends_on: str,
                             dtype: str = "finish_to_start") -> None:
        pool = await _pool()
        async with pool.acquire() as c:
            await c.execute(
                """INSERT INTO agent_task_dependencies(task_id,depends_on_task_id,dependency_type)
                   VALUES ($1,$2,$3) ON CONFLICT DO NOTHING""", task_id, depends_on, dtype)

    # -- atomik claim (çift-worker engeli) --
    @staticmethod
    async def claim_next(user_scope_hash: str, worker: str, *,
                         lease_s: int = 60) -> Optional[dict]:
        pool = await _pool()
        async with pool.acquire() as c:
            async with c.transaction():
                cand = await c.fetchrow(
                    """SELECT t.task_id FROM agent_tasks t
                       WHERE t.user_scope_hash=$1 AND t.status IN ('pending','ready')
                         AND NOT EXISTS (
                           SELECT 1 FROM agent_task_dependencies d
                           JOIN agent_tasks dt ON dt.task_id=d.depends_on_task_id
                           WHERE d.task_id=t.task_id AND dt.status <> 'completed')
                       ORDER BY t.priority DESC, t.created_at
                       FOR UPDATE SKIP LOCKED LIMIT 1""", user_scope_hash)
                if not cand:
                    return None
                row = await c.fetchrow(
                    """UPDATE agent_tasks
                       SET status='running', lease_owner=$2,
                           lease_expires_at = now() + make_interval(secs => $3),
                           started_at = COALESCE(started_at, now()),
                           heartbeat_at = now(), updated_at = now(), version = version+1
                       WHERE task_id=$1
                       RETURNING task_id, status, version, lease_owner""",
                    cand["task_id"], worker, float(lease_s))
                return dict(row)

    @staticmethod
    async def heartbeat(task_id: str, worker: str, *, lease_s: int = 60) -> bool:
        pool = await _pool()
        async with pool.acquire() as c:
            row = await c.fetchrow(
                """UPDATE agent_tasks
                   SET heartbeat_at=now(),
                       lease_expires_at = now() + make_interval(secs => $3),
                       updated_at=now()
                   WHERE task_id=$1 AND lease_owner=$2
                   RETURNING task_id""", task_id, worker, float(lease_s))
            return row is not None

    # -- durum geçişleri (optimistic locking) --
    @staticmethod
    async def mark(task_id: str, status: str, *, expected_version: Optional[int] = None,
                   error_code: Optional[str] = None, error_summary: Optional[str] = None,
                   output_ref: Optional[str] = None) -> bool:
        pool = await _pool()
        done = status in ("completed", "cancelled")
        async with pool.acquire() as c:
            if expected_version is not None:
                row = await c.fetchrow(
                    """UPDATE agent_tasks SET status=$2, error_code=$3, error_summary=$4,
                          output_ref=COALESCE($5,output_ref),
                          completed_at = CASE WHEN $6 THEN now() ELSE completed_at END,
                          updated_at=now(), version=version+1
                       WHERE task_id=$1 AND version=$7 RETURNING task_id""",
                    task_id, status, error_code, error_summary, output_ref, done, expected_version)
            else:
                row = await c.fetchrow(
                    """UPDATE agent_tasks SET status=$2, error_code=$3, error_summary=$4,
                          output_ref=COALESCE($5,output_ref),
                          completed_at = CASE WHEN $6 THEN now() ELSE completed_at END,
                          updated_at=now(), version=version+1
                       WHERE task_id=$1 RETURNING task_id""",
                    task_id, status, error_code, error_summary, output_ref, done)
            return row is not None

    # -- orphan sweep / crash recovery --
    @staticmethod
    async def sweep_orphans() -> list[dict]:
        pool = await _pool()
        async with pool.acquire() as c:
            rows = await c.fetch(
                """UPDATE agent_tasks SET
                     status = CASE WHEN retry_count < max_retry THEN 'pending' ELSE 'dead_letter' END,
                     retry_count = CASE WHEN retry_count < max_retry THEN retry_count+1 ELSE retry_count END,
                     lease_owner=NULL, lease_expires_at=NULL,
                     dead_letter_at = CASE WHEN retry_count < max_retry THEN NULL ELSE now() END,
                     error_code = CASE WHEN retry_count < max_retry THEN 'orphan_recovered' ELSE 'orphan_max_retry' END,
                     updated_at=now(), version=version+1
                   WHERE status IN ('running','waiting_tool','waiting_review')
                     AND lease_expires_at IS NOT NULL AND lease_expires_at <= now()
                   RETURNING task_id, status, retry_count""")
            return [dict(r) for r in rows]

    # ═══ FAZ 2: kalıcı iş kuyruğu (kind/payload + global claim + DLQ + backoff + event) ═══
    @staticmethod
    async def enqueue(*, kind: str, payload: Optional[dict] = None, task_id: Optional[str] = None,
                      user_scope_hash: str = "system", project_scope_hash: str = "",
                      objective: str = "", title: str = "", priority: int = 5,
                      max_retry: int = 2, idempotency_key: Optional[str] = None,
                      required_capability: str = "") -> dict:
        import json, hashlib, time as _t
        pool = await _pool()
        tid = task_id or ("job_" + hashlib.sha256(
            (kind + json.dumps(payload or {}, sort_keys=True) + str(_t.time())).encode()).hexdigest()[:20])
        async with pool.acquire() as c:
            if idempotency_key:
                ex = await c.fetchrow("SELECT task_id FROM agent_tasks WHERE idempotency_key=$1", idempotency_key)
                if ex:
                    return {"task_id": ex["task_id"], "created": False}
            row = await c.fetchrow(
                """INSERT INTO agent_tasks
                   (task_id,user_scope_hash,project_scope_hash,root_task_id,title,objective,
                    priority,max_retry,idempotency_key,kind,payload,required_capability,status)
                   VALUES ($1,$2,$3,$1,$4,$5,$6,$7,$8,$9,$10::jsonb,$11,'pending')
                   ON CONFLICT (task_id) DO NOTHING RETURNING task_id""",
                tid, user_scope_hash, project_scope_hash, title, objective, priority, max_retry,
                idempotency_key, kind, json.dumps(payload or {}), required_capability)
        return {"task_id": tid, "created": row is not None}

    # ═══ FAZ 4: DB worker registry (VPS + PC worker cross-process görünürlük) ═══
    @staticmethod
    async def register_worker(worker_id: str, *, kind: str = "", capabilities=None,
                              owner_hash: str = "", meta: Optional[dict] = None) -> None:
        import json
        pool = await _pool()
        async with pool.acquire() as c:
            await c.execute(
                """INSERT INTO hawk_workers (worker_id,kind,capabilities,owner_hash,meta,last_heartbeat,registered_at)
                   VALUES ($1,$2,$3,$4,$5::jsonb,now(),now())
                   ON CONFLICT (worker_id) DO UPDATE SET
                     kind=EXCLUDED.kind, capabilities=EXCLUDED.capabilities,
                     owner_hash=EXCLUDED.owner_hash, meta=EXCLUDED.meta, last_heartbeat=now()""",
                worker_id, kind, list(capabilities or []), owner_hash, json.dumps(meta or {}))

    @staticmethod
    async def worker_heartbeat(worker_id: str, *, current_task_id: Optional[str] = None) -> None:
        pool = await _pool()
        async with pool.acquire() as c:
            await c.execute(
                "UPDATE hawk_workers SET last_heartbeat=now(), current_task_id=$2 WHERE worker_id=$1",
                worker_id, current_task_id)

    @staticmethod
    async def list_workers(*, online_s: int = 90) -> list:
        pool = await _pool()
        async with pool.acquire() as c:
            rows = await c.fetch(
                """SELECT worker_id, kind, capabilities, current_task_id,
                          EXTRACT(EPOCH FROM (now()-last_heartbeat)) AS age_s
                   FROM hawk_workers ORDER BY last_heartbeat DESC""")
        out = []
        for r in rows:
            d = dict(r)
            d["online"] = float(d.pop("age_s") or 999) < online_s
            out.append(d)
        return out

    @staticmethod
    async def claim_next_global(worker: str, *, lease_s: int = 60,
                                kinds: Optional[list] = None,
                                caps: Optional[list] = None,
                                owner_hash: Optional[str] = None) -> Optional[dict]:
        """Scope-bağımsız atomik claim: hazır + bağımlılıkları biten + run_after geçmiş +
        duraklatılmamış + WORKER YETENEĞİNE uyan görevi kilitle (FOR UPDATE SKIP LOCKED →
        iki worker aynı görevi ALAMAZ). caps=None → yalnız yeteneksiz (genel) görevler.
        FAZ 2 izolasyon: owner_hash verilirse (PC worker) YALNIZ o sahibin görevleri claim edilir
        (cross-device/cross-owner claim ENGELLENİR). owner_hash=None → sistem worker (filtre yok)."""
        pool = await _pool()
        async with pool.acquire() as c:
            async with c.transaction():
                q = """SELECT t.task_id FROM agent_tasks t
                       WHERE t.status IN ('pending','ready')
                         AND (t.run_after IS NULL OR t.run_after <= now())
                         AND ($1::text[] IS NULL OR t.kind = ANY($1))
                         AND (t.required_capability = ''
                              OR ($2::text[] IS NOT NULL AND t.required_capability = ANY($2)))
                         AND ($3::text IS NULL OR t.user_scope_hash = $3)
                         AND NOT EXISTS (
                           SELECT 1 FROM agent_task_dependencies d
                           JOIN agent_tasks dt ON dt.task_id=d.depends_on_task_id
                           WHERE d.task_id=t.task_id AND dt.status <> 'completed')
                       ORDER BY t.priority DESC, t.created_at
                       FOR UPDATE SKIP LOCKED LIMIT 1"""
                cand = await c.fetchrow(q, kinds, caps, owner_hash)
                if not cand:
                    return None
                row = await c.fetchrow(
                    """UPDATE agent_tasks
                       SET status='running', lease_owner=$2,
                           lease_expires_at = now() + make_interval(secs => $3),
                           started_at = COALESCE(started_at, now()),
                           heartbeat_at=now(), updated_at=now(), version=version+1
                       WHERE task_id=$1
                       RETURNING task_id, kind, payload, objective, retry_count, max_retry,
                                 checkpoint_ref, version, lease_owner""",
                    cand["task_id"], worker, float(lease_s))
                d = dict(row)
                import json
                if isinstance(d.get("payload"), str):
                    try: d["payload"] = json.loads(d["payload"])
                    except Exception: d["payload"] = {}
                return d

    @staticmethod
    async def complete_task(task_id: str, worker: str, *, result_summary: str = "") -> bool:
        pool = await _pool()
        async with pool.acquire() as c:
            row = await c.fetchrow(
                """UPDATE agent_tasks SET status='completed', completed_at=now(),
                     result_summary=$3, lease_owner=NULL, lease_expires_at=NULL,
                     updated_at=now(), version=version+1
                   WHERE task_id=$1 AND lease_owner=$2 RETURNING task_id""",
                task_id, worker, result_summary[:500])
            return row is not None

    @staticmethod
    async def fail_task(task_id: str, worker: str, *, error_code: str = "error",
                        error_summary: str = "", backoff_s: int = 30) -> dict:
        """Hata: retry_count < max_retry → 'pending' + run_after backoff; değilse → 'dead_letter'."""
        pool = await _pool()
        async with pool.acquire() as c:
            row = await c.fetchrow(
                """UPDATE agent_tasks SET
                     status = CASE WHEN retry_count < max_retry THEN 'pending' ELSE 'dead_letter' END,
                     retry_count = CASE WHEN retry_count < max_retry THEN retry_count+1 ELSE retry_count END,
                     run_after = CASE WHEN retry_count < max_retry THEN now() + make_interval(secs => $4) ELSE run_after END,
                     dead_letter_at = CASE WHEN retry_count < max_retry THEN NULL ELSE now() END,
                     lease_owner=NULL, lease_expires_at=NULL,
                     error_code=$3, error_summary=$5, updated_at=now(), version=version+1
                   WHERE task_id=$1 AND (lease_owner=$2 OR lease_owner IS NULL)
                   RETURNING task_id, status, retry_count""",
                task_id, worker, error_code, float(backoff_s), (error_summary or "")[:500])
            return dict(row) if row else {}

    @staticmethod
    async def append_event(task_id: str, event_type: str, *, public_message: str = "",
                           meta: Optional[dict] = None) -> int:
        """Otomatik-sıralı kalıcı olay (agent_task_events). Döner: sequence."""
        import json
        pool = await _pool()
        async with pool.acquire() as c:
            seq = await c.fetchval(
                "SELECT COALESCE(MAX(sequence),0)+1 FROM agent_task_events WHERE task_id=$1", task_id)
            await c.execute(
                """INSERT INTO agent_task_events
                   (task_id,sequence,event_type,public_message,internal_metadata_redacted)
                   VALUES ($1,$2,$3,$4,$5) ON CONFLICT (task_id,sequence) DO NOTHING""",
                task_id, seq, event_type, public_message[:300], json.dumps(meta or {}))
            return seq

    @staticmethod
    async def save_checkpoint(task_id: str, state: dict) -> None:
        """Checkpoint state'i checkpoint_ref'e (JSON) yaz → resume için."""
        import json, hashlib, time as _t
        pool = await _pool()
        cid = "cp_" + hashlib.sha256((task_id + str(_t.time())).encode()).hexdigest()[:16]
        blob = json.dumps(state or {})[:8000]
        async with pool.acquire() as c:
            seq = await c.fetchval(
                "SELECT COALESCE(MAX(sequence),0)+1 FROM agent_task_checkpoints WHERE task_id=$1", task_id)
            await c.execute(
                """INSERT INTO agent_task_checkpoints (checkpoint_id,task_id,state_ref,sequence)
                   VALUES ($1,$2,$3,$4)""", cid, task_id, blob, seq)
            await c.execute(
                "UPDATE agent_tasks SET checkpoint_ref=$2, updated_at=now() WHERE task_id=$1",
                task_id, blob)

    @staticmethod
    async def load_checkpoint(task_id: str) -> dict:
        import json
        pool = await _pool()
        async with pool.acquire() as c:
            ref = await c.fetchval("SELECT checkpoint_ref FROM agent_tasks WHERE task_id=$1", task_id)
        try:
            return json.loads(ref) if ref else {}
        except Exception:
            return {}

    @staticmethod
    async def dead_letters(limit: int = 50) -> list:
        pool = await _pool()
        async with pool.acquire() as c:
            rows = await c.fetch(
                """SELECT task_id,kind,error_code,error_summary,retry_count,dead_letter_at
                   FROM agent_tasks WHERE status='dead_letter'
                   ORDER BY dead_letter_at DESC LIMIT $1""", limit)
        return [dict(r) for r in rows]

    @staticmethod
    async def pause(task_id: str) -> bool:
        pool = await _pool()
        async with pool.acquire() as c:
            row = await c.fetchrow(
                """UPDATE agent_tasks SET status='paused', updated_at=now(), version=version+1
                   WHERE task_id=$1 AND status NOT IN ('completed','cancelled')
                   RETURNING task_id""", task_id)
            return row is not None

    @staticmethod
    async def resume(task_id: str) -> bool:
        pool = await _pool()
        async with pool.acquire() as c:
            row = await c.fetchrow(
                """UPDATE agent_tasks SET status='pending', updated_at=now(), version=version+1
                   WHERE task_id=$1 AND status='paused' RETURNING task_id""", task_id)
            return row is not None

    @staticmethod
    async def cancel_subtree(task_id: str) -> list[str]:
        """Recursive iptal — çocuk node'lara yayılır; terminal node korunur."""
        pool = await _pool()
        async with pool.acquire() as c:
            rows = await c.fetch(
                """WITH RECURSIVE sub AS (
                     SELECT task_id FROM agent_tasks WHERE task_id=$1
                     UNION ALL
                     SELECT t.task_id FROM agent_tasks t JOIN sub ON t.parent_task_id=sub.task_id)
                   UPDATE agent_tasks SET status='cancelled', completed_at=now(),
                          updated_at=now(), version=version+1
                   WHERE task_id IN (SELECT task_id FROM sub)
                     AND status NOT IN ('completed','cancelled')
                   RETURNING task_id""", task_id)
            return [r["task_id"] for r in rows]

    # -- olay / kanıt / review / checkpoint --
    @staticmethod
    async def record_event(task_id: str, sequence: int, event_type: str, *,
                           public_message: str = "", internal_metadata: Optional[dict] = None) -> None:
        import json
        pool = await _pool()
        async with pool.acquire() as c:
            await c.execute(
                """INSERT INTO agent_task_events
                   (task_id,sequence,event_type,public_message,internal_metadata_redacted)
                   VALUES ($1,$2,$3,$4,$5) ON CONFLICT (task_id,sequence) DO NOTHING""",
                task_id, sequence, event_type, public_message,
                json.dumps(internal_metadata or {}))

    @staticmethod
    async def add_evidence(*, evidence_id: str, task_id: str, evidence_type: str,
                           source_ref: str = "", content_hash: str = "",
                           generated_by: str = "", reproducible: bool = False,
                           confidence: float = 0.0) -> None:
        pool = await _pool()
        async with pool.acquire() as c:
            await c.execute(
                """INSERT INTO agent_task_evidence
                   (evidence_id,task_id,evidence_type,source_ref,content_hash,generated_by,
                    reproducible,confidence)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8) ON CONFLICT (evidence_id) DO NOTHING""",
                evidence_id, task_id, evidence_type, source_ref, content_hash,
                generated_by, reproducible, confidence)

    @staticmethod
    async def add_review(*, review_id: str, task_id: str, executor_agent_id: str,
                         reviewer_agent_id: str, verdict: str, score: Optional[float] = None,
                         evidence_ids: Optional[list[str]] = None) -> bool:
        """executor==reviewer ise DB check reddeder → False döner (kanıt: bağımsız reviewer)."""
        pool = await _pool()
        async with pool.acquire() as c:
            try:
                await c.execute(
                    """INSERT INTO agent_task_reviews
                       (review_id,task_id,executor_agent_id,reviewer_agent_id,verdict,score,evidence_ids)
                       VALUES ($1,$2,$3,$4,$5,$6,$7)""",
                    review_id, task_id, executor_agent_id, reviewer_agent_id, verdict,
                    score, evidence_ids or [])
                return True
            except Exception:
                return False

    @staticmethod
    async def checkpoint(*, checkpoint_id: str, task_id: str, state_ref: str,
                         sequence: int) -> None:
        pool = await _pool()
        async with pool.acquire() as c:
            await c.execute(
                """INSERT INTO agent_task_checkpoints(checkpoint_id,task_id,state_ref,sequence)
                   VALUES ($1,$2,$3,$4) ON CONFLICT (task_id,sequence) DO NOTHING""",
                checkpoint_id, task_id, state_ref, sequence)

    @staticmethod
    async def get_task(task_id: str) -> Optional[dict]:
        pool = await _pool()
        async with pool.acquire() as c:
            row = await c.fetchrow("SELECT * FROM agent_tasks WHERE task_id=$1", task_id)
            return dict(row) if row else None

    @staticmethod
    async def purge_scope(user_scope_hash: str) -> int:
        """Test/temizlik: bir scope'un tüm görevlerini siler (FK cascade)."""
        pool = await _pool()
        async with pool.acquire() as c:
            res = await c.execute("DELETE FROM agent_tasks WHERE user_scope_hash=$1", user_scope_hash)
            return int(res.split()[-1]) if res else 0
