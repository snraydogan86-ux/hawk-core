"""
HAWK Otomatik Haftalık QA Agent (Md.8/34/35).

Gerçek kullanıcı gibi (Chromium) siteyi gezer (çok sayfa + iPhone viewport),
bulguları sınıflar, rapor üretir (DB + markdown), backlog'a görev yazar,
GÜVENLİ küçük düzeltmeleri (opsiyonel) otomatik uygular; büyük/riskli olanları
backlog'a bırakır. Cron ile haftalık çalışır.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from typing import Any, Dict, List

from core.pg_memory import _get_pool

log = logging.getLogger("hawk.weekly_qa")

_QA_DIR = "/app/data/qa"


def _classify(finding: str) -> Dict[str, Any]:
    """Bulgu → severity + auto_fixable (güvenli küçük düzeltme mi?)."""
    f = finding.lower()
    if "[api]" in f or "[js]" in f or "[console]" in f:
        return {"severity": "critical", "auto_fixable": False}   # backend/JS → insan
    if "taşma" in f or "[medya]" in f or "bozuk" in f:
        return {"severity": "high", "auto_fixable": True}        # CSS/responsive/asset → güvenli
    if "[dil]" in f:
        return {"severity": "medium", "auto_fixable": True}      # eksik çeviri → güvenli
    return {"severity": "medium", "auto_fixable": False}


def _fingerprint(finding: str) -> str:
    return hashlib.sha256(finding.encode()).hexdigest()[:32]


async def _log_backlog(findings: List[str]) -> int:
    """Bulguları backlog'a yaz (fingerprint ile tekilleştir). Yeni eklenen sayısını döner."""
    if not findings:
        return 0
    pool = await _get_pool()
    added = 0
    async with pool.acquire() as conn:
        for f in findings:
            c = _classify(f)
            r = await conn.fetchrow(
                """INSERT INTO hawk_backlog (title, detail, source, severity, auto_fixable, fingerprint)
                   VALUES ($1,$2,'qa',$3,$4,$5)
                   ON CONFLICT (fingerprint) DO UPDATE SET updated_at=now()
                   RETURNING (xmax = 0) AS inserted""",
                f[:200], f, c["severity"], c["auto_fixable"], _fingerprint(f))
            if r and r["inserted"]:
                added += 1
    return added


def _write_markdown(report: Dict[str, Any]) -> str:
    os.makedirs(_QA_DIR, exist_ok=True)
    path = os.path.join(_QA_DIR, "latest.md")
    lines = [
        f"# HAWK Haftalık QA Raporu — {report.get('status')}",
        "",
        f"- Durum: **{report.get('status')}**",
        f"- Bulgu: **{report.get('finding_count', 0)}**",
        f"- Gezilen sayfalar: {', '.join(report.get('visited', []))}",
        f"- Ekran görüntüleri: {len(report.get('screenshots', []))}",
        "",
        "## Bulgular",
    ]
    if report.get("findings"):
        for f in report["findings"]:
            sev = _classify(f)["severity"].upper()
            lines.append(f"- **[{sev}]** {f}")
    else:
        lines.append("- ✅ Bulgu yok — sistem temiz.")
    if report.get("screenshots"):
        lines += ["", "## Ekran Görüntüleri"] + [f"- {s}" for s in report["screenshots"]]
    md = "\n".join(lines) + "\n"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(md)
    return path


async def run_weekly_qa(*, notify: bool = False, autofix: bool = False) -> Dict[str, Any]:
    """Haftalık QA turu. Rapor + backlog + (opsiyonel) güvenli autofix döner."""
    from core.live_qa import run_qa
    qa = await run_qa(notify=False)
    findings = qa.get("findings", []) or []

    # Rapor kaydet (DB)
    pool = await _get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO hawk_qa_reports (status, finding_count, data) VALUES ($1,$2,$3)",
            qa.get("status", "UNKNOWN"), len(findings), json.dumps(qa, ensure_ascii=False))
        # ikinci kopya improvement_reports'a (mevcut panel)
        try:
            await conn.execute(
                "INSERT INTO improvement_reports (title, summary, data) VALUES ($1,$2,$3)",
                "Haftalık QA", f"{qa.get('status')} — {len(findings)} bulgu",
                json.dumps(qa, ensure_ascii=False))
        except Exception:
            pass

    md_path = _write_markdown(qa)
    added = await _log_backlog(findings)

    result = {
        "ok": True,
        "status": qa.get("status"),
        "finding_count": len(findings),
        "findings": findings,
        "visited": qa.get("visited", []),
        "screenshots": qa.get("screenshots", []),
        "backlog_new": added,
        "report_md": md_path,
        "auto_fixable": [f for f in findings if _classify(f)["auto_fixable"]],
    }

    # Güvenli küçük düzeltme (opsiyonel; qa_autofix kendi güvenli sınırına uyar)
    if autofix and result["auto_fixable"]:
        try:
            from core.qa_autofix import run_qa_autofix
            result["autofix"] = await run_qa_autofix(notify=False)
        except Exception as e:
            result["autofix_error"] = str(e)[:200]

    if notify:
        try:
            from core.communication_hub import notify_soner
            msg = (f"🧪 Haftalık QA: {result['status']} — {result['finding_count']} bulgu, "
                   f"{added} yeni backlog.")
            if findings:
                msg += "\n" + "\n".join("• " + f for f in findings[:6])
            await notify_soner(msg, priority="high" if result["status"] == "RED" else "normal")
        except Exception:
            pass

    return result
