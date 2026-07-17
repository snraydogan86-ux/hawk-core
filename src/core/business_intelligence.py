"""
HAWK Business Intelligence + CEO Mode — çekirdek motor.

İlkeler (ETİK / GÜVENLİK — değişmez):
  * HAWK ASLA otomatik dış mesaj göndermez.
  * Otomatik DM / issue / comment / PR / e-posta YOK.
  * Sadece: fırsat bulur, puanlar, raporlar, TASLAK hazırlar.
  * Tüm dış iletişim Soner Aydoğan onayına bağlıdır.

Bu modül canlı sisteme additive'dir; mevcut chat/voice/gold/billing/token/mail
sistemlerine dokunmaz. DB erişimi paylaşılan pool üzerindendir (db.get_pool).
Tüm endpoint'ler admin korumalıdır (router katmanında).
"""
from __future__ import annotations

import os
import json
import asyncio
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

# ── Sabitler ──────────────────────────────────────────────────────────────────
OWN_REPO = os.getenv("HAWK_GH_REPO", "snraydogan86-ux/HAWK-AI")
GH_API = "https://api.github.com"

# İleride aktifleştirilecek kaynaklar — şimdilik "hazır iskelet" (otomatik tarama yok)
SOURCE_ADAPTERS = {
    "github": True,        # aktif
    "hackernews": False,   # iskelet — ileride
    "reddit": False,       # iskelet — ileride
    "producthunt": False,  # iskelet — ileride
    "vc": False,           # iskelet — ileride
}


async def _pool():
    from db import get_pool
    return await get_pool()


# ── GitHub erişimi (yalnız OKUMA — yazma/spam YOK) ────────────────────────────
def _gh_get_sync(path: str) -> Optional[Dict[str, Any]]:
    url = path if path.startswith("http") else f"{GH_API}{path}"
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "HAWK-BI"}
    token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"_error": f"http_{e.code}"}
    except Exception as e:
        return {"_error": type(e).__name__}


async def _gh_get(path: str) -> Optional[Dict[str, Any]]:
    return await asyncio.to_thread(_gh_get_sync, path)


async def fetch_github_metrics(repo: str, store: bool = True) -> Dict[str, Any]:
    """Bir repo'nun star/fork/issue/watcher + son release bilgisini çeker (salt-okuma)."""
    data = await _gh_get(f"/repos/{repo}")
    if not data or data.get("_error"):
        return {"repo": repo, "ok": False, "error": (data or {}).get("_error", "no_data")}
    rel = await _gh_get(f"/repos/{repo}/releases/latest")
    latest_release = ""
    if rel and not rel.get("_error"):
        latest_release = rel.get("tag_name") or rel.get("name") or ""
    metrics = {
        "repo": repo,
        "ok": True,
        "stars": int(data.get("stargazers_count", 0)),
        "forks": int(data.get("forks_count", 0)),
        "open_issues": int(data.get("open_issues_count", 0)),
        "watchers": int(data.get("subscribers_count", data.get("watchers_count", 0))),
        "latest_release": latest_release,
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }
    if store:
        try:
            pool = await _pool()
            async with pool.acquire() as con:
                await con.execute(
                    """INSERT INTO public.github_metrics
                       (repo, stars, forks, open_issues, watchers, latest_release, meta)
                       VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb)""",
                    repo, metrics["stars"], metrics["forks"], metrics["open_issues"],
                    metrics["watchers"], latest_release, json.dumps({"full_name": data.get("full_name", repo)}),
                )
        except Exception as e:
            metrics["store_error"] = type(e).__name__
    return metrics


async def github_overview() -> Dict[str, Any]:
    """Kendi repo + delta (önceki ölçüme göre değişim)."""
    cur = await fetch_github_metrics(OWN_REPO, store=True)
    delta = {}
    try:
        pool = await _pool()
        async with pool.acquire() as con:
            rows = await con.fetch(
                """SELECT stars, forks, open_issues, watchers, captured_at
                   FROM public.github_metrics WHERE repo=$1
                   ORDER BY captured_at DESC LIMIT 2""", OWN_REPO)
            if len(rows) >= 2 and cur.get("ok"):
                prev = rows[1]
                delta = {
                    "stars": cur["stars"] - prev["stars"],
                    "forks": cur["forks"] - prev["forks"],
                    "open_issues": cur["open_issues"] - prev["open_issues"],
                    "watchers": cur["watchers"] - prev["watchers"],
                }
    except Exception:
        pass
    return {"repo": OWN_REPO, "current": cur, "delta": delta, "adapters": SOURCE_ADAPTERS}


# ── Fırsat puanlama + yönetimi ────────────────────────────────────────────────
def score_opportunity(opp: Dict[str, Any]) -> int:
    """Basit, açıklanabilir heuristik puan (0..100). İleride ML ile geliştirilebilir."""
    score = 0
    kind = (opp.get("kind") or "other").lower()
    score += {"investor": 35, "customer": 30, "partner": 25}.get(kind, 10)
    text = f"{opp.get('title','')} {opp.get('description','')}".lower()
    signals = ["ai", "agent", "investment", "fund", "seed", "enterprise",
               "saas", "integration", "partnership", "yatırım", "ortaklık", "müşteri"]
    score += min(30, sum(6 for s in signals if s in text))
    if opp.get("url"):
        score += 10
    src = (opp.get("source") or "").lower()
    score += {"vc": 15, "producthunt": 12, "hackernews": 10, "github": 8}.get(src, 5)
    return max(0, min(100, score))


async def add_opportunity(kind: str, title: str, source: str = "manual",
                          url: str = "", description: str = "",
                          meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    opp = {"kind": kind, "title": title, "source": source, "url": url, "description": description}
    score = score_opportunity(opp)
    pool = await _pool()
    async with pool.acquire() as con:
        row = await con.fetchrow(
            """INSERT INTO public.business_opportunities
               (kind, title, source, url, description, score, status, meta)
               VALUES ($1,$2,$3,$4,$5,$6,'new',$7::jsonb) RETURNING id""",
            kind, title, source, url, description, score, json.dumps(meta or {}))
    return {"id": row["id"], "score": score, "status": "new"}


async def list_opportunities(kind: Optional[str] = None, status: Optional[str] = None,
                             limit: int = 50) -> List[Dict[str, Any]]:
    pool = await _pool()
    q = "SELECT * FROM public.business_opportunities WHERE 1=1"
    args: List[Any] = []
    if kind:
        args.append(kind); q += f" AND kind=${len(args)}"
    if status:
        args.append(status); q += f" AND status=${len(args)}"
    args.append(min(limit, 200)); q += f" ORDER BY score DESC, id DESC LIMIT ${len(args)}"
    async with pool.acquire() as con:
        rows = await con.fetch(q, *args)
    return [_row(r) for r in rows]


# ── Rakip watchlist ───────────────────────────────────────────────────────────
async def list_competitors(active_only: bool = True) -> List[Dict[str, Any]]:
    pool = await _pool()
    async with pool.acquire() as con:
        if active_only:
            rows = await con.fetch("SELECT * FROM public.competitor_watchlist WHERE active ORDER BY id")
        else:
            rows = await con.fetch("SELECT * FROM public.competitor_watchlist ORDER BY id")
    return [_row(r) for r in rows]


async def competitor_analysis() -> Dict[str, Any]:
    """Watchlist'teki repo'ların güncel GitHub metriklerini çeker (salt-okuma)."""
    comps = await list_competitors(active_only=True)
    results = []
    for c in comps:
        if c.get("repo"):
            m = await fetch_github_metrics(c["repo"], store=True)
            results.append({"name": c["name"], **m})
        else:
            results.append({"name": c["name"], "repo": "", "ok": False, "note": "no_repo"})
    return {"count": len(results), "competitors": results}


# ── Raporlar ──────────────────────────────────────────────────────────────────
async def generate_report(period: str = "daily") -> Dict[str, Any]:
    gh = await github_overview()
    opps = await list_opportunities(limit=200)
    by_status: Dict[str, int] = {}
    for o in opps:
        by_status[o["status"]] = by_status.get(o["status"], 0) + 1
    summary = (f"{period.upper()} rapor — Repo {gh['repo']}: "
               f"{gh.get('current',{}).get('stars','?')}★ / "
               f"{gh.get('current',{}).get('open_issues','?')} açık issue. "
               f"Fırsatlar: {len(opps)} kayıt.")
    data = {"github": gh, "opportunities_by_status": by_status,
            "top_opportunities": opps[:5]}
    pool = await _pool()
    async with pool.acquire() as con:
        row = await con.fetchrow(
            """INSERT INTO public.business_reports (period, title, summary, data)
               VALUES ($1,$2,$3,$4::jsonb) RETURNING id, created_at""",
            period, f"{period} report", summary, json.dumps(data, default=str))
    return {"id": row["id"], "period": period, "summary": summary,
            "created_at": str(row["created_at"]), "data": data}


# ── Sistem sağlığı (CEO brief için) ───────────────────────────────────────────
async def system_health() -> Dict[str, Any]:
    health = {"db": False, "tables": 0, "api": "unknown"}
    try:
        pool = await _pool()
        async with pool.acquire() as con:
            health["db"] = (await con.fetchval("SELECT 1")) == 1
            health["tables"] = await con.fetchval(
                "SELECT count(*) FROM pg_tables WHERE schemaname='public'")
    except Exception as e:
        health["db_error"] = type(e).__name__
    try:
        def _ping():
            req = urllib.request.Request("http://localhost:8000/api/health")
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status
        code = await asyncio.to_thread(_ping)
        health["api"] = "ok" if code == 200 else f"http_{code}"
    except Exception:
        health["api"] = "unreachable"
    return health


# ── CEO Mode brief ────────────────────────────────────────────────────────────
async def generate_ceo_brief(store: bool = True, force: bool = False) -> Dict[str, Any]:
    # COST GUARDIAN: son 1 saat içinde brief üretildiyse 72B/araç işini tekrarlama
    if not force:
        try:
            pool = await _pool()
            async with pool.acquire() as con:
                row = await con.fetchrow(
                    """SELECT summary, data, created_at,
                              EXTRACT(EPOCH FROM (now()-created_at)) AS age
                       FROM public.ceo_daily_briefs ORDER BY id DESC LIMIT 1""")
            if row and float(row["age"]) < 3600:
                data = row["data"]
                if isinstance(data, str):
                    data = json.loads(data)
                return {"summary": row["summary"], "data": data, "cached": True,
                        "last_update": str(row["created_at"]),
                        "age_seconds": int(row["age"]),
                        "notice": "Cache: son 1 saat içindeki brief gösterildi (yeni 72B/araç çağrısı yok)."}
        except Exception:
            pass

    gh = await github_overview()
    health = await system_health()
    opps = await list_opportunities(limit=100)
    investors = [o for o in opps if o["kind"] == "investor"][:5]
    cur = gh.get("current", {})
    delta = gh.get("delta", {})

    yesterday = []
    if delta:
        if delta.get("stars"): yesterday.append(f"GitHub yıldız: {delta['stars']:+d}")
        if delta.get("forks"): yesterday.append(f"Fork: {delta['forks']:+d}")
        if delta.get("open_issues"): yesterday.append(f"Açık issue: {delta['open_issues']:+d}")
    if not yesterday:
        yesterday.append("Ölçülebilir değişiklik yok (veya ilk ölçüm).")

    # Canlı araç sinyalleri (hawk_tools — local-first, kritik işlem yok)
    tools_snapshot: Dict[str, Any] = {}
    try:
        from core.hawk_tools import executor as _tx
        for it in await _tx.run_tools_direct(
                ["get_system_health", "get_gpu_cloud_cost_status", "get_billing_status", "get_token_status"]):
            tools_snapshot[it.get("tool")] = it.get("result") if it.get("ok") else {"error": it.get("error")}
    except Exception:
        tools_snapshot = {}

    risks = []
    if cur.get("open_issues", 0) > 20:
        risks.append("Açık issue sayısı yüksek — triage gerekebilir.")
    if not cur.get("latest_release"):
        risks.append("Henüz yayınlanmış release yok — ilk sürüm etiketlenmeli.")
    if health.get("api") != "ok":
        risks.append("API health kontrolü 'ok' dönmedi — incelenmeli.")
    _sysh = tools_snapshot.get("get_system_health", {})
    for _c, _st in (_sysh.get("components") or {}).items():
        if _st != "ok":
            risks.append(f"Bileşen erişilemiyor: {_c}")
    if (tools_snapshot.get("get_gpu_cloud_cost_status") or {}).get("status") in ("watch", "unknown"):
        risks.append("GPU cloud maliyet izlenmeli (spend rate).")
    if not risks:
        risks.append("Belirgin acil risk yok.")

    token_tasks = [
        "HAWK Token görünürlüğü: listing başvuru durumunu izle.",
        "Tokenomik/whitepaper bağlantılarının güncelliğini doğrula.",
        "On-chain üyelik doğrulaması metriklerini gözden geçir.",
    ]

    top3 = [
        "Bugünün en yüksek puanlı fırsatını incele ve taslak hazırlat.",
        "GitHub açık issue'ları triage et / README & sürüm notlarını güncelle.",
        "Token listing ve görünürlük görevlerinden birini ilerlet.",
    ]

    data = {
        "yesterday": yesterday,
        "system_health": health,
        "github": cur,
        "github_delta": delta,
        "metrics": {"opportunities_total": len(opps),
                    "investors": len(investors)},
        "token_tasks": token_tasks,
        "opportunities": investors,
        "top3_priorities": top3,
        "risks": risks,
        "tools": tools_snapshot,
    }
    summary = (f"CEO Brief — Sistem: DB {'OK' if health.get('db') else 'HATA'}, "
               f"API {health.get('api')}. GitHub: {cur.get('stars','?')}★. "
               f"Fırsat: {len(opps)}. Bugünün 3 önceliği hazır.")
    if store:
        try:
            pool = await _pool()
            async with pool.acquire() as con:
                await con.execute(
                    """INSERT INTO public.ceo_daily_briefs (summary, data)
                       VALUES ($1,$2::jsonb)""", summary, json.dumps(data, default=str))
        except Exception as e:
            data["store_error"] = type(e).__name__
    return {"summary": summary, "data": data,
            "generated_at": datetime.now(timezone.utc).isoformat()}


# ── Outreach TASLAK (gönderilmez — yalnız onay için hazırlanır) ───────────────
async def draft_outreach(opportunity_id: int) -> Dict[str, Any]:
    """Bir fırsat için iletişim TASLAĞI hazırlar. ASLA göndermez — Soner onayına sunar."""
    pool = await _pool()
    async with pool.acquire() as con:
        opp = await con.fetchrow(
            "SELECT * FROM public.business_opportunities WHERE id=$1", opportunity_id)
        if not opp:
            return {"ok": False, "error": "opportunity_not_found"}
        opp = _row(opp)
        kind = opp["kind"]
        greeting = {"investor": "Merhaba,", "customer": "Merhaba,",
                    "partner": "Merhaba,"}.get(kind, "Merhaba,")
        pitch = {
            "investor": "HAWK AI; otonom, gizliliğe saygılı bir yapay zeka platformudur. "
                        "Yatırım fırsatını görüşmek isteriz.",
            "customer": "HAWK AI ile iş süreçlerinizi otomatikleştirebilir, "
                        "AI destekli çözümlerden faydalanabilirsiniz.",
            "partner": "HAWK AI ekosistemi için bir iş birliği fırsatı görüyoruz.",
        }.get(kind, "HAWK AI hakkında sizinle iletişime geçmek isteriz.")
        draft = (f"{greeting}\n\n{pitch}\n\n"
                 f"(İlgili: {opp.get('title','')})\n\n"
                 f"Saygılarımla,\nSoner Aydoğan — HAWK AI")
        await con.execute(
            "UPDATE public.business_opportunities SET draft=$1, status='drafted', updated_at=now() WHERE id=$2",
            draft, opportunity_id)
    return {"ok": True, "opportunity_id": opportunity_id, "status": "drafted",
            "draft": draft,
            "notice": "TASLAK hazırlandı. Otomatik gönderim YOK — Soner Aydoğan onayı gerekir."}


async def refresh_all() -> Dict[str, Any]:
    """Kendi repo + rakip metriklerini çeker, günlük rapor üretir. DIŞ MESAJ YOK."""
    gh = await github_overview()
    comp = await competitor_analysis()
    report = await generate_report("daily")
    return {"ok": True, "github": gh, "competitors": comp,
            "report_id": report["id"],
            "notice": "Yalnızca veri toplandı + rapor üretildi. Hiçbir dış mesaj gönderilmedi."}


# ── yardımcı ──────────────────────────────────────────────────────────────────
def _row(r) -> Dict[str, Any]:
    d = dict(r)
    for k, v in list(d.items()):
        if isinstance(v, (datetime,)):
            d[k] = v.isoformat()
        elif hasattr(v, "isoformat"):
            d[k] = v.isoformat()
    return d
