"""
HAWK Self-Improvement Agent System — çekirdek.

AMAÇ: HAWK kendi görevlerini üretir, yenilik düşünür, planlar, alt ajanlar
tanımlar ve GÜVENLİ SINIRLAR içinde geliştirme süreci başlatır.

DEĞİŞMEZ KURALLAR (kod seviyesinde uygulanır):
  * Kritik işlemler ASLA otomatik yürütülmez — yalnızca approval_requests'e düşer.
    (git commit/push, deploy, docker restart, DB migration, ödeme, token/cüzdan,
     mail, kullanıcı verisi silme, public paylaşım, dış API key değişikliği)
  * Ana dala direkt commit yok. İnsanlara otomatik spam/DM/issue/comment yok.
  * HAWK sadece plan, taslak, görev, test planı ve rapor üretir.
  * Local-first: analiz/planlama yerel heuristiklerle yapılır; dış model yalnızca
    açıkça etkinleştirilirse (local runtime) ve yalnızca zenginleştirme için kullanılır.

Bu modül additive'dir; mevcut hawk_core_*/self_healer/orchestrator sistemlerine
dokunmaz. DB erişimi paylaşılan pool (db.get_pool) üzerindendir.
"""
from __future__ import annotations

import os
import json
import asyncio
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# Onay zorunlu kritik işlemler
CRITICAL_ACTIONS = {
    "git_commit", "git_push", "deploy", "docker_restart", "db_migration",
    "payment", "token", "mail", "delete_user_data", "public_share", "api_key_change",
}

# Analiz kategorileri
ANALYSIS_CATEGORIES = [
    "missing_features", "broken_areas", "ux", "security", "performance",
    "revenue", "token", "membership", "mobile", "github", "documentation",
]

# Görev tipleri
TASK_TYPES = [
    "bugfix", "feature", "security", "performance", "business",
    "token", "billing", "mobile", "documentation", "investor", "marketing",
]

# Agent factory — örnek ajan şablonları (otomatik kritik işlem YOK)
DEFAULT_AGENTS = [
    {"name": "Security Agent", "role": "Güvenlik analizi", "authority_level": 1,
     "capabilities": ["audit", "risk_report", "propose_fix"]},
    {"name": "GitHub Agent", "role": "Repo durumu & metrik", "authority_level": 1,
     "capabilities": ["read_metrics", "propose_issue_triage"]},
    {"name": "Business Intelligence Agent", "role": "Fırsat & rapor", "authority_level": 1,
     "capabilities": ["score_opportunity", "report"]},
    {"name": "Token Agent", "role": "Token entegrasyonu", "authority_level": 1,
     "capabilities": ["propose_integration"]},
    {"name": "Billing Agent", "role": "Gelir/üyelik", "authority_level": 1,
     "capabilities": ["analyze_funnel", "propose_plan"]},
    {"name": "Mobile App Agent", "role": "Mobil hazırlık", "authority_level": 1,
     "capabilities": ["pwa_check", "propose_store_prep"]},
    {"name": "Investor Agent", "role": "Yatırımcı fırsatı", "authority_level": 1,
     "capabilities": ["draft_outreach"]},
    {"name": "Customer Support Agent", "role": "Destek", "authority_level": 1,
     "capabilities": ["triage", "draft_reply"]},
    {"name": "UI/UX Agent", "role": "Arayüz iyileştirme", "authority_level": 1,
     "capabilities": ["ux_audit", "propose_ui"]},
    {"name": "Test Agent", "role": "Test planı & koşum", "authority_level": 1,
     "capabilities": ["write_test_plan", "run_local_tests"]},
    {"name": "Deployment Guard Agent", "role": "Deploy bekçisi", "authority_level": 0,
     "capabilities": ["block_unsafe_deploy", "require_approval"]},
]


async def _pool():
    from db import get_pool
    return await get_pool()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row(r) -> Dict[str, Any]:
    d = dict(r)
    for k, v in list(d.items()):
        if hasattr(v, "isoformat"):
            d[k] = v.isoformat()
    return d


# ── Local-first model erişimi (varsayılan: yerel heuristik) ───────────────────
def _local_runtime_enabled() -> bool:
    return (os.getenv("LOCAL_RUNTIME", "").lower() in ("1", "true", "yes")
            and bool(os.getenv("LOCAL_RUNTIME") or os.getenv("LOCAL_RUNTIME")))


async def llm_local(prompt: str, fallback: str = "") -> str:
    """Yalnız local runtime etkinse yerel modeli kullanır; aksi halde fallback döner.
    Dış (ücretli) API ASLA bu modülden otomatik çağrılmaz (maliyet kontrolü)."""
    if not _local_runtime_enabled():
        return fallback
    base = (os.getenv("LOCAL_RUNTIME") or os.getenv("LOCAL_RUNTIME", "")).rstrip("/")
    model = os.getenv("LOCAL_RUNTIME", "local-model")
    try:
        def _call():
            body = json.dumps({"model": model, "prompt": prompt, "stream": False}).encode()
            req = urllib.request.Request(f"{base}/api/generate", data=body,
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode()).get("response", fallback)
        return await asyncio.to_thread(_call)
    except Exception:
        return fallback


# ── ANALİZ (11 kategori, local + DB + GitHub) ─────────────────────────────────
async def analyze() -> Dict[str, Any]:
    findings: List[Dict[str, Any]] = []

    def add(cat, sev, title, desc=""):
        findings.append({"category": cat, "severity": sev, "title": title, "description": desc})

    # GitHub durumu
    gh_cur: Dict[str, Any] = {}
    try:
        from core import business_intelligence as bi
        gh = await bi.github_overview()
        gh_cur = gh.get("current", {}) or {}
        if gh_cur.get("ok"):
            if gh_cur.get("open_issues", 0) > 20:
                add("github", "medium", "Açık issue sayısı yüksek",
                    f"{gh_cur['open_issues']} açık issue — triage gerekebilir.")
            if not gh_cur.get("latest_release"):
                add("github", "low", "Yayınlanmış release yok",
                    "İlk sürüm etiketlenmeli (v0.1.0).")
            if gh_cur.get("stars", 0) < 10:
                add("revenue", "low", "Görünürlük düşük",
                    "Star sayısı düşük — pazarlama/launch planı düşünülebilir.")
    except Exception as e:
        add("reliability", "low", "GitHub analizi yapılamadı", type(e).__name__)

    # Sistem sağlığı / DB
    try:
        pool = await _pool()
        async with pool.acquire() as con:
            tables = await con.fetchval("SELECT count(*) FROM pg_tables WHERE schemaname='public'")
            opp = await con.fetchval("SELECT count(*) FROM public.business_opportunities")
            briefs = await con.fetchval("SELECT count(*) FROM public.ceo_daily_briefs")
        if opp == 0:
            add("revenue", "low", "Fırsat havuzu boş",
                "Henüz iş fırsatı kaydı yok — BI kaynakları beslenmeli.")
        if briefs == 0:
            add("ux", "low", "CEO brief üretilmemiş", "İlk CEO brief'i tetiklenmeli.")
    except Exception as e:
        add("reliability", "high", "DB analizi başarısız", type(e).__name__)

    # Statik/yapısal öneriler (local heuristik — container-safe)
    add("mobile", "medium", "Mobil uygulama mağaza hazırlığı",
        "PWA mevcut; App Store / Play Store paketleme planı hazırlanmalı.")
    add("security", "low", "Düzenli sır taraması",
        "CI'da otomatik secret-scan adımı eklenmeli (push protection'a ek).")
    add("dependency", "medium", "Dış model bağımlılığını azalt",
        "Local-first (local runtime) planlama/değerlendirme yaygınlaştırılmalı; dış API yalnız kritik işlerde.")
    add("documentation", "low", "Doküman tamlığı",
        "Whitepaper/README/CHANGELOG sürüm notları güncel tutulmalı.")
    add("performance", "low", "Performans izleme",
        "Endpoint gecikme metrikleri toplanmalı (p95).")

    # ── GERÇEK ARAÇ SİNYALLERİ (hawk_tools executor ile canlı veri) ──────────
    tool_snapshot: Dict[str, Any] = {}
    try:
        from core.hawk_tools import executor as _tx
        sig = await _tx.run_tools_direct([
            "get_system_health", "get_github_status", "get_gpu_cloud_cost_status",
            "get_billing_status", "get_token_status"])
        for item in sig:
            name = item.get("tool"); res = item.get("result") or {}
            tool_snapshot[name] = res if item.get("ok") else {"error": item.get("error")}
            if name == "get_system_health":
                for comp, st in (res.get("components") or {}).items():
                    if st not in ("ok",):
                        add("reliability", "high", f"Bileşen erişilemiyor: {comp}",
                            f"get_system_health: {comp}={st}")
            elif name == "get_gpu_cloud_cost_status":
                if res.get("status") in ("watch", "unknown"):
                    add("cost", "medium", "GPU cloud maliyet izlemesi",
                        res.get("note", "Spend rate izlenmeli."))
            elif name == "get_billing_status":
                provs = res.get("providers_configured") or {}
                if provs and not any(provs.values()):
                    add("revenue", "medium", "Ödeme sağlayıcı yapılandırılmamış",
                        "Hiçbir billing sağlayıcı aktif görünmüyor.")
            elif name == "get_token_status":
                if res.get("listing_status") not in ("listed",):
                    add("token", "low", "Token listing devam ediyor",
                        f"listing_status={res.get('listing_status')}")
    except Exception as e:
        add("reliability", "low", "Araç sinyalleri toplanamadı", type(e).__name__)

    # Riskleri kaydet (açık aynı başlık varsa tekrar etme)
    stored = 0
    try:
        pool = await _pool()
        async with pool.acquire() as con:
            for f in findings:
                exists = await con.fetchval(
                    "SELECT 1 FROM public.risk_logs WHERE title=$1 AND status='open' LIMIT 1", f["title"])
                if not exists:
                    await con.execute(
                        """INSERT INTO public.risk_logs (category, severity, title, description)
                           VALUES ($1,$2,$3,$4)""",
                        f["category"], f["severity"], f["title"], f["description"])
                    stored += 1
    except Exception:
        pass

    return {"findings": findings, "stored_new_risks": stored,
            "github": gh_cur, "categories": ANALYSIS_CATEGORIES,
            "tool_snapshot": tool_snapshot}


# ── TASK GENERATOR ────────────────────────────────────────────────────────────
def _finding_to_task(f: Dict[str, Any]) -> Dict[str, Any]:
    cat = f["category"]
    type_map = {
        "github": "documentation", "revenue": "business", "ux": "feature",
        "security": "security", "performance": "performance", "mobile": "mobile",
        "documentation": "documentation", "dependency": "performance",
        "token": "token", "membership": "billing", "reliability": "bugfix",
    }
    sev = f["severity"]
    risk = sev if sev in ("low", "medium", "high", "critical") else "low"
    prio = {"critical": 1, "high": 2, "medium": 4, "low": 7}.get(sev, 5)
    ttype = type_map.get(cat, "feature")
    return {
        "type": ttype, "title": f["title"], "description": f["description"],
        "priority": prio, "risk_level": risk,
        "files": [], "expected_result": f"{f['title']} ele alınır / iyileştirilir.",
        "test_plan": "İlgili endpoint/health testi + manuel doğrulama + sır taraması.",
        # Riskli her şey + kritik kategoriler onay gerektirir
        "requires_approval": risk in ("high", "critical") or ttype in ("security", "token", "billing"),
    }


async def generate_tasks(limit: int = 20) -> Dict[str, Any]:
    analysis = await analyze()
    tasks = [_finding_to_task(f) for f in analysis["findings"]][:limit]
    created = []
    pool = await _pool()
    async with pool.acquire() as con:
        for t in tasks:
            # aynı başlıkta proposed görev varsa tekrar üretme
            exists = await con.fetchval(
                "SELECT 1 FROM public.self_tasks WHERE title=$1 AND status IN ('proposed','approved','in_progress') LIMIT 1",
                t["title"])
            if exists:
                continue
            row = await con.fetchrow(
                """INSERT INTO public.self_tasks
                   (type,title,description,priority,risk_level,files,expected_result,test_plan,requires_approval,created_by)
                   VALUES ($1,$2,$3,$4,$5,$6::jsonb,$7,$8,$9,'self-improver') RETURNING id""",
                t["type"], t["title"], t["description"], t["priority"], t["risk_level"],
                json.dumps(t["files"]), t["expected_result"], t["test_plan"], t["requires_approval"])
            created.append({"id": row["id"], **t})
    return {"ok": True, "generated": len(created), "tasks": created,
            "notice": "Görevler TASLAK olarak üretildi — yürütme için onay/insan gerekir."}


async def list_tasks(status: Optional[str] = None, ttype: Optional[str] = None,
                     limit: int = 100) -> List[Dict[str, Any]]:
    pool = await _pool()
    q = "SELECT * FROM public.self_tasks WHERE 1=1"
    args: List[Any] = []
    if status:
        args.append(status); q += f" AND status=${len(args)}"
    if ttype:
        args.append(ttype); q += f" AND type=${len(args)}"
    args.append(min(limit, 300)); q += f" ORDER BY priority, id DESC LIMIT ${len(args)}"
    async with pool.acquire() as con:
        rows = await con.fetch(q, *args)
    return [_row(r) for r in rows]


# ── AGENT FACTORY ─────────────────────────────────────────────────────────────
async def create_agent(name: str, role: str = "", capabilities: Optional[List[str]] = None,
                       authority_level: int = 0, meta: Optional[Dict] = None) -> Dict[str, Any]:
    # Güvenlik: hiçbir ajan 2'den yüksek yetki alamaz (otomatik kritik işlem imkânsız)
    authority_level = max(0, min(2, int(authority_level)))
    pool = await _pool()
    async with pool.acquire() as con:
        row = await con.fetchrow(
            """INSERT INTO public.self_agents (name, role, capabilities, authority_level, meta)
               VALUES ($1,$2,$3::jsonb,$4,$5::jsonb)
               ON CONFLICT (name) DO UPDATE SET role=EXCLUDED.role,
                 capabilities=EXCLUDED.capabilities, authority_level=EXCLUDED.authority_level,
                 updated_at=now()
               RETURNING id""",
            name, role, json.dumps(capabilities or []), authority_level, json.dumps(meta or {}))
    return {"ok": True, "id": row["id"], "name": name, "authority_level": authority_level,
            "notice": "Ajan tanımlandı. Yetki ≤2 — kritik işlemler yine onaya tabidir."}


async def seed_default_agents() -> Dict[str, Any]:
    created = 0
    for a in DEFAULT_AGENTS:
        await create_agent(a["name"], a["role"], a["capabilities"], a["authority_level"])
        created += 1
    return {"ok": True, "seeded": created}


async def list_agents() -> List[Dict[str, Any]]:
    pool = await _pool()
    async with pool.acquire() as con:
        rows = await con.fetch("SELECT * FROM public.self_agents ORDER BY id")
    return [_row(r) for r in rows]


# ── PLAN ──────────────────────────────────────────────────────────────────────
async def get_or_build_plan() -> Dict[str, Any]:
    tasks = await list_tasks(status="proposed", limit=50)
    items = [{"task_id": t["id"], "title": t["title"], "type": t["type"],
              "priority": t["priority"], "risk_level": t["risk_level"],
              "requires_approval": t["requires_approval"]} for t in tasks]
    items.sort(key=lambda x: x["priority"])
    summary = (f"{len(items)} önerilen görev. "
               f"İlk 3 öncelik: " + "; ".join(i["title"] for i in items[:3]) if items
               else "Henüz önerilen görev yok — generate-tasks çalıştırın.")
    pool = await _pool()
    async with pool.acquire() as con:
        row = await con.fetchrow(
            """INSERT INTO public.self_plans (title, summary, items, status)
               VALUES ($1,$2,$3::jsonb,'draft') RETURNING id, created_at""",
            "Geliştirme Planı", summary, json.dumps(items))
    return {"ok": True, "plan_id": row["id"], "summary": summary,
            "created_at": str(row["created_at"]), "items": items}


# ── APPROVAL GATE ─────────────────────────────────────────────────────────────
async def request_approval(action_type: str, title: str, payload: Optional[Dict] = None,
                           risk_level: str = "high", requested_by: str = "self-improver") -> Dict[str, Any]:
    """Kritik işlem TALEBİ oluşturur — işlemi ASLA yürütmez."""
    pool = await _pool()
    async with pool.acquire() as con:
        row = await con.fetchrow(
            """INSERT INTO public.approval_requests
               (action_type, title, payload, risk_level, requested_by)
               VALUES ($1,$2,$3::jsonb,$4,$5) RETURNING id""",
            action_type, title, json.dumps(payload or {}), risk_level, requested_by)
    return {"ok": True, "approval_id": row["id"], "status": "pending",
            "notice": f"'{action_type}' işlemi ONAY BEKLİYOR — otomatik yürütülmedi. "
                      "Son karar Soner Aydoğan'a aittir."}


async def list_pending_approvals() -> List[Dict[str, Any]]:
    pool = await _pool()
    async with pool.acquire() as con:
        rows = await con.fetch(
            "SELECT * FROM public.approval_requests WHERE status='pending' ORDER BY created_at DESC")
    return [_row(r) for r in rows]


async def decide_approval(approval_id: int, decision: str, note: str = "",
                          decided_by: str = "Soner Aydoğan") -> Dict[str, Any]:
    """Onay/ret KAYDEDER. NOT: 'approved' bile kritik altyapı işlemini OTOMATİK
    yürütmez — yürütme insan elindedir (deploy/migration/push vb.)."""
    if decision not in ("approved", "rejected"):
        return {"ok": False, "error": "decision must be approved|rejected"}
    pool = await _pool()
    async with pool.acquire() as con:
        row = await con.fetchrow(
            """UPDATE public.approval_requests
               SET status=$1, decision_note=$2, decided_by=$3, decided_at=now()
               WHERE id=$4 RETURNING action_type""",
            decision, note, decided_by, approval_id)
    if not row:
        return {"ok": False, "error": "approval_not_found"}
    return {"ok": True, "approval_id": approval_id, "decision": decision,
            "executed": False,
            "notice": "Karar kaydedildi. Onaylansa dahi kritik işlem OTOMATİK yürütülmez "
                      "(deploy/migration/push insan tarafından yapılır)."}


# ── ÖNERİ / RAPOR ─────────────────────────────────────────────────────────────
async def propose_improvement(title: str, description: str = "",
                              category: str = "feature") -> Dict[str, Any]:
    """Bir iyileştirme önerisini görev olarak kaydeder (taslak)."""
    pool = await _pool()
    risk = "low"
    prio = 5
    async with pool.acquire() as con:
        row = await con.fetchrow(
            """INSERT INTO public.self_tasks
               (type,title,description,priority,risk_level,requires_approval,created_by)
               VALUES ($1,$2,$3,$4,$5,$6,'human-or-agent') RETURNING id""",
            category if category in TASK_TYPES else "feature",
            title, description, prio, risk,
            category in ("security", "token", "billing"))
    return {"ok": True, "task_id": row["id"], "status": "proposed"}


async def list_risks(status: str = "open", limit: int = 100) -> List[Dict[str, Any]]:
    pool = await _pool()
    async with pool.acquire() as con:
        if status == "all":
            rows = await con.fetch(
                "SELECT * FROM public.risk_logs ORDER BY created_at DESC LIMIT $1", min(limit, 300))
        else:
            rows = await con.fetch(
                "SELECT * FROM public.risk_logs WHERE status=$1 ORDER BY created_at DESC LIMIT $2",
                status, min(limit, 300))
    return [_row(r) for r in rows]


async def generate_report() -> Dict[str, Any]:
    tasks = await list_tasks(limit=300)
    risks = await list_risks(status="open", limit=300)
    pending = await list_pending_approvals()
    agents = await list_agents()
    by_type: Dict[str, int] = {}
    for t in tasks:
        by_type[t["type"]] = by_type.get(t["type"], 0) + 1
    summary = (f"Self-Improvement raporu — {len(tasks)} görev, {len(risks)} açık risk, "
               f"{len(pending)} onay bekleyen, {len(agents)} ajan.")
    data = {"tasks_total": len(tasks), "tasks_by_type": by_type,
            "open_risks": len(risks), "pending_approvals": len(pending),
            "agents": len(agents),
            "top_risks": risks[:5], "top_tasks": tasks[:5]}
    pool = await _pool()
    async with pool.acquire() as con:
        row = await con.fetchrow(
            """INSERT INTO public.improvement_reports (title, summary, data)
               VALUES ($1,$2,$3::jsonb) RETURNING id, created_at""",
            "Self-Improvement Report", summary, json.dumps(data, default=str))
    return {"ok": True, "report_id": row["id"], "summary": summary,
            "created_at": str(row["created_at"]), "data": data}


# ── TAM DÜŞÜNME DÖNGÜSÜ (analiz→risk→görev→plan→ajan→rapor; hiçbiri yürütülmez) ─
async def think(force: bool = False) -> Dict[str, Any]:
    # COST GUARDIAN: son think hâlâ geçerliyse (30 dk) tekrar tam döngü çalıştırma
    _CK = "think:cycle"
    if not force:
        try:
            from core import cost_guardian as _cg
            hit = await _cg.cache_get("think", _cg.req_hash(_CK, scope="think"), ttl=1800)
            if hit:
                r = dict(hit["response"]); r["cached"] = True
                r["age_seconds"] = hit.get("age_seconds")
                r["notice"] = "Cache: son 30 dk içindeki think döngüsü gösterildi (yeni çağrı yok)."
                return r
        except Exception:
            pass

    analysis = await analyze()
    gen = await generate_tasks()
    plan = await get_or_build_plan()
    agents = await list_agents()
    if not agents:
        await seed_default_agents()
        agents = await list_agents()
    report = await generate_report()
    snap = analysis.get("tool_snapshot") or {}
    sys_h = (snap.get("get_system_health") or {})
    result = {
        "ok": True,
        "analysis": {"new_risks": analysis["stored_new_risks"], "findings": len(analysis["findings"])},
        "tool_snapshot": {
            "system": sys_h.get("overall") or sys_h.get("status"),
            "healthy_components": sys_h.get("healthy"),
            "gpu_cloud": (snap.get("get_gpu_cloud_cost_status") or {}).get("status"),
            "github_stars": (snap.get("get_github_status") or {}).get("stars"),
            "token_listing": (snap.get("get_token_status") or {}).get("listing_status"),
        },
        "tasks_generated": gen["generated"],
        "plan_id": plan["plan_id"],
        "agents": len(agents),
        "report_id": report["report_id"],
        "notice": "Tam döngü tamamlandı: analiz + görev + plan + ajan + rapor. "
                  "HİÇBİR kritik işlem yürütülmedi — tümü taslak/öneri, onay Soner'de.",
    }
    try:
        from core import cost_guardian as _cg
        await _cg.cache_put("think", _cg.req_hash(_CK, scope="think"), result, tier="tier3", ttl=1800)
    except Exception:
        pass
    return result
