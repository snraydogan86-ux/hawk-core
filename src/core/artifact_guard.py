"""
HAWK Artifact Guard — sahte indirme linki / uydurma artifact'e karşı ORTAK doğrulama.
(Spec Bölüm 11: chat + Builder + Workspace için ortak.)

Kural: HAWK bir dosya/ZIP/indirme linkini "hazır" diye SUNMADAN ÖNCE dosyanın
GERÇEKTEN diskte var olduğu + SHA256 + boyut doğrulanır. Var olmayan linkler
kullanıcıya gönderilmeden temizlenir → "hallucinated download" kapatılır.
"""
from __future__ import annotations
import hashlib
import os
import re

UPLOAD_DIR = os.getenv("HAWK_FILES_DIR", "/app/uploads/files")

# /api/dl/<fname>  (query'li veya query'siz, tam URL veya göreli)
_DL_RE = re.compile(r"(?:https?://[^\s)\"']+)?/api/dl/([A-Za-z0-9._-]+)")


def _safe_name(fname: str) -> str:
    return os.path.basename(str(fname or "")).strip()


def verify_artifact(fname: str, *, min_bytes: int = 1) -> dict:
    """Dosya diskte GERÇEKTEN var mı? SHA256 + boyut. Path traversal reddi."""
    safe = _safe_name(fname)
    out = {"fname": safe, "exists": False, "bytes": 0, "sha256": "", "ok": False}
    if not safe or safe != fname.strip().split("/")[-1] or ".." in fname:
        out["error"] = "bad_name"
        return out
    path = os.path.join(UPLOAD_DIR, safe)
    if not os.path.isfile(path):
        out["error"] = "not_found"
        return out
    size = os.path.getsize(path)
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    out.update(exists=True, bytes=size, sha256=h.hexdigest(),
               ok=(size >= min_bytes))
    return out


def artifact_ready(fname: str, *, min_bytes: int = 1) -> bool:
    """Kısa yol: artifact gerçekten indirilebilir mi (dosya var + boyut yeterli)."""
    return verify_artifact(fname, min_bytes=min_bytes).get("ok", False)


def scrub_unverified_links(text: str) -> tuple[str, list]:
    """LLM çıktısındaki /api/dl/<fname> linklerini DOĞRULA. Diskte olmayanları
    (uydurma) linkten arındırıp dürüst bir nota çevir. (temiz_metin, kaldırılanlar)."""
    if not text or "/api/dl/" not in text:
        return text, []
    removed: list = []

    def _repl(m):
        fname = m.group(1)
        if artifact_ready(fname):
            return m.group(0)          # gerçek dosya → dokunma
        removed.append(fname)
        return "[indirme bağlantısı üretilemedi — dosya henüz hazır değil]"

    clean = _DL_RE.sub(_repl, text)
    return clean, removed
