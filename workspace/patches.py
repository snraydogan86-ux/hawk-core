"""
HAWK CORE — Patch öneri sistemi (GÜVENLİ).

Akış (Soner'in istediği):
  1. propose_patch() → LLM düzeltme önerir, DB'ye 'pending' yazar, SONER'İ ARAR.
  2. Soner onaylar (approve) → status 'approved'.
  3. apply_patch() → SADECE onaylıysa uygular: derleme kontrolü + yedek + git commit.

Onaysız HİÇBİR patch uygulanmaz. Onaylı patch bile derlenmezse uygulanmaz (geri sarılır).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from typing import Any, Dict, List, Optional

from core.pg_memory import _get_pool

from .engine import brain_chat
from . import memory as M

_APP_DIR = "/app"


async def propose_patch(finding_id: Optional[int] = None, hint: str = "") -> Dict[str, Any]:
    """Bir bulgu için düzeltme öner (uygulamaz!), kaydet, Soner'i ara."""
    pool = await _get_pool()
    finding = None
    if finding_id:
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM hawk_core_findings WHERE id=$1", finding_id)
            finding = dict(row) if row else None

    context = hint
    if finding:
        context = f"Bulgu: {finding['title']}\nDetay: {finding['detail']}\nDosya: {finding['file']}"

    prompt = (
        "Sen HAWK'ın düzeltme önericisisin. Aşağıdaki soruna GÜVENLİ bir düzeltme öner. "
        "SADECE öneri (kod yazma/uygulama yok). Kısa: ne, neden, hangi dosya, risk.\n\n"
        f"{context}\n\nÖneri:"
    )
    r = await brain_chat([{"role": "user", "content": prompt}], temperature=0.3, max_tokens=400)
    description = r.get("text", "") or "Öneri üretilemedi."
    title = (finding["title"] if finding else (hint[:80] or "Patch önerisi"))
    target_file = finding["file"] if finding else ""

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO hawk_core_patches (finding_id, title, description, target_file, status)
               VALUES ($1,$2,$3,$4,'pending') RETURNING id""",
            finding_id, title[:500], description[:6000], target_file[:300])
        patch_id = int(row["id"])

    await M.remember_episode("event", f"[self-improve] patch önerisi #{patch_id}: {title}")

    # Soner'i KONUŞMALI ara (onay için) — ne/niçin anlatır, sesli onay alır, kapatmaz
    called = False
    try:
        from routers.phone_agent import start_approval_call
        res = await start_approval_call(
            title=title,
            explanation=f"Bir düzeltme önerdim: {title}. {description[:200]}",
            patch_id=patch_id)
        called = bool(res.get("ok"))
    except Exception:
        pass

    return {"ok": True, "patch_id": patch_id, "title": title,
            "description": description, "called_soner": called, "status": "pending"}


async def list_patches(status: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        if status:
            rows = await conn.fetch(
                "SELECT id, title, target_file, status, created_at FROM hawk_core_patches WHERE status=$1 ORDER BY id DESC LIMIT $2",
                status, limit)
        else:
            rows = await conn.fetch(
                "SELECT id, title, target_file, status, created_at FROM hawk_core_patches ORDER BY id DESC LIMIT $1", limit)
        return [dict(r) for r in rows]


async def set_status(patch_id: int, status: str) -> Dict[str, Any]:
    """Soner onayı/reddi — status: approved | rejected."""
    if status not in ("approved", "rejected"):
        return {"ok": False, "error": "status approved|rejected olmalı"}
    pool = await _get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE hawk_core_patches SET status=$1, resolved_at=now() WHERE id=$2 AND status='pending'",
            status, patch_id)
    await M.remember_episode("event", f"[self-improve] patch #{patch_id} → {status} (Soner)")
    return {"ok": True, "patch_id": patch_id, "status": status}


async def apply_patch(patch_id: int, new_content: Optional[str] = None) -> Dict[str, Any]:
    """SADECE onaylı patch'i uygula. Güvenlik: derleme kontrolü + yedek + git commit.
    new_content verilirse target_file'ı onunla değiştirir (derlenebilirse)."""
    pool = await _get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM hawk_core_patches WHERE id=$1", patch_id)
    if not row:
        return {"ok": False, "error": "patch bulunamadı"}
    if row["status"] != "approved":
        return {"ok": False, "error": f"patch onaylı değil (durum: {row['status']}). Önce Soner onaylamalı."}
    if not new_content:
        return {"ok": False, "error": "uygulanacak içerik (new_content) verilmedi — güvenlik gereği boş patch uygulanmaz"}

    target = row["target_file"]
    if not target:
        return {"ok": False, "error": "hedef dosya yok"}
    path = target if target.startswith("/") else os.path.join(_APP_DIR, target)
    if not os.path.exists(path):
        return {"ok": False, "error": f"hedef dosya yok: {path}"}

    # 1) Derleme kontrolü (Python ise) — bozuksa UYGULAMA
    if path.endswith(".py"):
        tmp = path + ".hawk_tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(new_content)
            import py_compile
            py_compile.compile(tmp, doraise=True)
        except Exception as e:  # noqa: BLE001
            try:
                os.remove(tmp)
            except Exception:
                pass
            return {"ok": False, "error": f"yeni içerik derlenmiyor, uygulanmadı: {e}"}
        finally:
            try:
                os.remove(tmp)
            except Exception:
                pass

    # 2) Yedek
    backup = f"{path}.bak_patch_{patch_id}_{int(time.time())}"
    shutil.copy2(path, backup)
    # 3) Uygula
    with open(path, "w", encoding="utf-8") as f:
        f.write(new_content)
    # 4) git commit (kayıt altına al)
    commit_out = ""
    try:
        subprocess.run(["git", "-C", _APP_DIR, "add", target], capture_output=True, timeout=15)
        cp = subprocess.run(
            ["git", "-C", _APP_DIR, "commit", "-m", f"hawk-self-improve: patch #{patch_id} {row['title'][:60]}"],
            capture_output=True, timeout=15, text=True)
        commit_out = (cp.stdout or cp.stderr)[:200]
    except Exception as e:  # noqa: BLE001
        commit_out = f"commit atlandı: {e}"

    async with pool.acquire() as conn:
        await conn.execute("UPDATE hawk_core_patches SET status='applied', resolved_at=now() WHERE id=$1", patch_id)
    await M.remember_episode("action", f"[self-improve] patch #{patch_id} UYGULANDI: {row['title']}")
    return {"ok": True, "patch_id": patch_id, "applied": True, "backup": backup, "commit": commit_out}
