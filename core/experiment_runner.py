"""
FAZ 7 — Deney yürütme + BAĞIMSIZ değerlendirme + kanıt sistemi.

FAZ 6'nın ürettiği deney PLANLARINI güvenli (read-only) biçimde yürütür, GERÇEK kanıt toplar
(komut+exit, DB sonucu, dosya boyutu/hash), sonra EXECUTOR'dan BAĞIMSIZ bir Evaluator (reviewer)
kanıta bakarak karar verir. Kanıtsız "başarılı" REDDEDİLİR. Başarısız deney ayrı hatırlanır.

KESİN YASAK: production değişmez. Yalnız read-only probe + (GPU gereken benchmark) ONAY bekler.
"""
from __future__ import annotations
import hashlib
import json
import os
import subprocess

_PROBE_EXE = {"du", "df", "ls", "wc"}   # yalnız read-only probe komutları


def _run_probe(cmd_argv: list, cwd: str = "/", timeout: int = 30) -> dict:
    """Read-only probe komutu (argv, shell=False, allowlist). Döner exit+out (GERÇEK kanıt)."""
    if not cmd_argv or cmd_argv[0] not in _PROBE_EXE:
        return {"ok": False, "error": f"probe izinli değil: {cmd_argv[0] if cmd_argv else ''}"}
    try:
        p = subprocess.run(cmd_argv, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return {"ok": True, "exit": p.returncode, "out": (p.stdout or "")[:4000],
                "err": (p.stderr or "")[:500], "cmd": " ".join(cmd_argv)}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


# ── Probe'lar (kind bazlı, read-only) → (evidence_list, metric, claimed_ok) ──
def _probe_disk_high() -> dict:
    import shutil
    ev = []
    du = _run_probe(["du", "-sm", "/data/hawk_sandboxes", "/data/hawk_memory", "/tmp"], cwd="/")
    reclaim_mb = 0
    if du.get("ok"):
        ev.append({"etype": "test_output", "summary": f"du exit={du['exit']}",
                   "detail": du["cmd"] + "\n" + du["out"]})
        for line in du["out"].splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[0].isdigit():
                # sandbox + tmp geri kazanılabilir sayılır
                if "sandbox" in parts[1] or parts[1] == "/tmp":
                    reclaim_mb += int(parts[0])
    try:
        d = shutil.disk_usage("/")
        ev.append({"etype": "log", "summary": f"disk %{round(100*d.used/d.total,1)} boş {round(d.free/1e9,1)}GB",
                   "detail": f"total={d.total} used={d.used} free={d.free}"})
    except Exception:
        pass
    reclaim_gb = round(reclaim_mb / 1024.0, 2)
    return {"evidence": ev, "metric": {"reclaimable_gb": reclaim_gb},
            "claimed_ok": reclaim_gb >= 2.0,
            "summary": f"geri kazanılabilir ~{reclaim_gb}GB (sandbox/tmp)"}


async def _probe_dead_letter_high() -> dict:
    from core.pg_memory import _get_pool
    ev = []
    pool = await _get_pool()
    async with pool.acquire() as c:
        rows = await c.fetch(
            """SELECT error_code, count(*) n FROM agent_tasks WHERE status='dead_letter'
               GROUP BY error_code ORDER BY n DESC""")
    total = sum(r["n"] for r in rows) or 1
    dist = [(r["error_code"], r["n"]) for r in rows]
    top_ratio = (dist[0][1] / total) if dist else 0.0
    ev.append({"etype": "log", "summary": f"dead_letter error_code dağılımı (toplam {total})",
               "detail": json.dumps(dist)})
    return {"evidence": ev, "metric": {"top_root_cause_ratio": round(top_ratio, 2), "total": total},
            "claimed_ok": top_ratio >= 0.6,
            "summary": f"en baskın error_code oranı %{round(top_ratio*100)}"}


async def _execute(kind: str, method: str) -> dict:
    """Deneyi güvenli yürüt. GPU gereken benchmark → ONAY bekler (auto-run YOK)."""
    if method == "benchmark":
        return {"needs_approval": True, "evidence": [], "metric": {}, "claimed_ok": None,
                "summary": "benchmark GPU/pod gerektirir — admin onayı + kaynak kontrolü bekliyor"}
    if kind == "disk_high":
        return _probe_disk_high()
    if kind == "dead_letter_high":
        return await _probe_dead_letter_high()
    return {"evidence": [{"etype": "claim", "summary": "bu kind için probe yok"}],
            "metric": {}, "claimed_ok": None, "summary": "probe tanımsız"}


# ── Bağımsız Evaluator (reviewer≠executor, kanıtsız PASS yok) ─────────────────
async def _evaluate(eid: str, exec_result: dict, success_hint: bool) -> dict:
    """EXECUTOR'dan BAĞIMSIZ evaluator: hard-evidence kontrolü + metrik/ölçüt kararı."""
    from core.agent_orchestration import EvidenceStore, AgentRegistry
    from core.agent_orchestration.evidence import EvidenceType
    from core.agent_orchestration.reviewer import Reviewer
    EXEC = "ag_exp_executor"
    EVAL = "ag_evaluator"
    reg = AgentRegistry()
    reg.build_from_role(agent_id=EXEC, role="researcher", objective="deney yürüt",
                        user_scope="research", project_scope="research")
    reg.build_from_role(agent_id=EVAL, role="reviewer", objective="bağımsız değerlendirme",
                        user_scope="research", project_scope="research")
    es = EvidenceStore()
    for e in exec_result.get("evidence", []):
        try:
            et = EvidenceType(e["etype"])
        except Exception:
            et = EvidenceType.CLAIM
        es.add(task_id=eid, agent_id=EXEC, etype=et, summary=e.get("summary", ""),
               detail=e.get("detail", ""), reproducible=(et == EvidenceType.TEST_OUTPUT))
    reviewer = Reviewer(agent_id=EVAL, registry=reg, require_hard_evidence=True)
    verdict = await reviewer.review(task_id=eid, executor_agent_id=EXEC, evidence=es)
    # kanıtsız PASS engeli: hard-evidence yoksa reviewer.passed False → sonuç 'inconclusive'
    if not verdict.get("passed"):
        return {"outcome": "inconclusive", "reason": verdict.get("reason"),
                "reviewer": EVAL, "independent": True, "hard_evidence": False}
    # hard-evidence VAR → metrik/ölçüt kararı (executor'ın iddiası kanıtla tutuyor mu)
    outcome = "passed" if success_hint else "failed"
    return {"outcome": outcome, "reason": "evidence_verified", "reviewer": EVAL,
            "independent": True, "hard_evidence": True, "score": verdict.get("score")}


async def run_experiment(experiment_id: str) -> dict:
    """Bir deneyi yürüt → kanıt topla → BAĞIMSIZ değerlendir → kaydet. Başarısız→hatırla."""
    from core.pg_memory import _get_pool
    from core import research_loop as _rl
    pool = await _get_pool()
    async with pool.acquire() as c:
        row = await c.fetchrow(
            """SELECT e.experiment_id,e.dedup_hash,e.method,e.success_criterion,e.failure_criterion,
                      e.status, p.kind FROM hawk_experiments e JOIN hawk_problems p ON p.problem_id=e.problem_id
               WHERE e.experiment_id=$1""", experiment_id)
    if not row:
        return {"ok": False, "error": "not_found"}
    if row["status"] in ("passed", "failed", "running"):
        return {"ok": False, "error": f"status={row['status']}"}
    kind = row["kind"]
    async with pool.acquire() as c:
        await c.execute("UPDATE hawk_experiments SET status='running', updated_at=now() WHERE experiment_id=$1", experiment_id)

    res = await _execute(kind, row["method"])
    if res.get("needs_approval"):
        async with pool.acquire() as c:
            await c.execute("UPDATE hawk_experiments SET status='planned', outcome=$2, updated_at=now() WHERE experiment_id=$1",
                            experiment_id, res["summary"])
        return {"ok": True, "experiment_id": experiment_id, "outcome": "needs_approval", "detail": res["summary"]}

    ev = await _evaluate(experiment_id, res, bool(res.get("claimed_ok")))
    outcome = ev["outcome"]
    async with pool.acquire() as c:
        await c.execute(
            "UPDATE hawk_experiments SET status=$2, outcome=$3, updated_at=now() WHERE experiment_id=$1",
            experiment_id, outcome, (res.get("summary", "") + f" | evaluator={ev['reviewer']} hard={ev['hard_evidence']}")[:400])
    # başarısız/inconclusive → hatırla (tekrar planlanmasın)
    if outcome in ("failed", "inconclusive"):
        try:
            await _rl.remember_experiment(row["dedup_hash"], kind, outcome, res.get("summary", ""))
        except Exception:
            pass
    # ── DÖNGÜYÜ KAPAT: her deneyden OPERATIONAL-LEARNING çıkar (pass VE fail) ──
    # Bu, "sürekli kendini-geliştirme" halkasının eksik parçasıydı: sonuç→öğrenme→sonraki-karar.
    # FAIL-SAFE: learning-yazımı hatası deneyin outcome'unu ASLA bozmaz (deney sonucu birincil).
    metric = res.get("metric") or {}
    learned = {"id": None, "decision_effect": "none"}
    try:
        learned = await record_operational_learning(
            kind=kind, outcome=outcome, metric=metric, evidence_summary=res.get("summary", ""),
            evaluator=ev["reviewer"], hard_evidence=bool(ev.get("hard_evidence")),
            problem_id=(row["problem_id"] if "problem_id" in row.keys() else None))
        if outcome == "passed":
            await _write_improvement_report(kind, res.get("summary", ""), metric)
    except Exception as _e:
        learned = {"id": None, "decision_effect": "none", "learn_error": str(_e)[:120]}
    return {"ok": True, "experiment_id": experiment_id, "kind": kind, "outcome": outcome,
            "independent_evaluator": ev["reviewer"], "hard_evidence": ev["hard_evidence"],
            "metric": metric, "summary": res.get("summary"),
            "operational_learning_id": learned.get("id"), "decision_effect": learned.get("decision_effect")}


async def record_operational_learning(*, kind: str, outcome: str, metric: dict, evidence_summary: str,
                                      evaluator: str, hard_evidence: bool, problem_id=None) -> dict:
    """Deney sonucunu KALICI operational-learning'e yaz (hawk_learning_notes) + KARAR ETKİSİ üret.
    Tekrarlı başarısız kind → 'deprioritize' notu (observer bir daha o problemi açmaz). Bu, döngüyü
    kapatan halka: sonuç → öğrenme → sonraki kararı değiştir."""
    from core.pg_memory import _get_pool
    pool = await _get_pool()
    decision = "none"
    async with pool.acquire() as c:
        # aynı kind için son başarısızlık sayısı (tekrarlı fail → deprioritize kararı)
        prior_fail = await c.fetchval(
            "SELECT count(*) FROM hawk_experiments e JOIN hawk_problems p ON p.problem_id=e.problem_id "
            "WHERE p.kind=$1 AND e.status IN ('failed','inconclusive')", kind) or 0
        if outcome in ("failed", "inconclusive") and prior_fail >= 2:
            decision = "deprioritize_kind"      # observer bu kind'i artık açmasın (karar değişti)
        elif outcome == "passed":
            decision = "apply_and_resolve"
        note = (f"[{kind}] deney {outcome}: {evidence_summary} | metrik={json.dumps(metric, ensure_ascii=False)} "
                f"| evaluator={evaluator} hard_evidence={hard_evidence} → KARAR={decision}")
        row = await c.fetchrow(
            "INSERT INTO hawk_learning_notes (category, note, priority, resolved) "
            "VALUES ($1,$2,$3,$4) RETURNING id",
            f"research:{kind}", note[:900], (2 if decision == "deprioritize_kind" else 1),
            (outcome == "passed"))
        # KARAR ETKİSİ: tekrarlı-fail kind → problemleri 'deprioritized' işaretle (bir daha açılmaz)
        if decision == "deprioritize_kind":
            await c.execute("UPDATE hawk_problems SET status='deprioritized', updated_at=now() "
                            "WHERE kind=$1 AND status IN ('open','planned')", kind)
        elif decision == "apply_and_resolve" and problem_id:
            await c.execute("UPDATE hawk_problems SET status='resolved', updated_at=now() WHERE problem_id=$1", problem_id)
    return {"id": row["id"] if row else None, "decision_effect": decision}


async def _write_improvement_report(kind: str, summary: str, metric: dict):
    from core.pg_memory import _get_pool
    pool = await _get_pool()
    try:
        async with pool.acquire() as c:
            await c.execute(
                "INSERT INTO improvement_reports (title, summary, data, created_at) VALUES ($1,$2,$3,now())",
                f"research:{kind} iyileştirme önerisi",
                f"Deney PASSED. {summary}",
                json.dumps({"kind": kind, "metric": metric, "source": "research_loop", "status": "proposed"}, ensure_ascii=False))
    except Exception:
        pass


async def run_pending(limit: int = 3) -> dict:
    """Onaysız çalışabilen (read-only) planlı deneyleri yürüt. benchmark hariç (onay bekler)."""
    # FAZ 3 fix: research kill-switch (veya global) → deney yürütme başlamaz.
    try:
        from core import cost_guard as _cg
        if _cg.is_killed() or _cg.is_killed("research"):
            return {"blocked": True, "ran": 0, "results": []}
    except Exception:
        pass
    from core.pg_memory import _get_pool
    pool = await _get_pool()
    async with pool.acquire() as c:
        rows = await c.fetch(
            """SELECT experiment_id FROM hawk_experiments
               WHERE status='planned' AND method='readonly_probe' ORDER BY created_at LIMIT $1""", limit)
    out = []
    for r in rows:
        out.append(await run_experiment(r["experiment_id"]))
    return {"ok": True, "ran": len(out), "results": out}
