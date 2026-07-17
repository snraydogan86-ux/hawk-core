"""
HAWK Citation / Kaynak yapısı — web arama sonuçlarından yapılandırılmış
kaynaklar (sources[]) üretir ve cevaba eklenebilir "Kaynaklar" bölümü oluşturur.

Amaç: doğru, kaynaklı cevap. web_search çıktısı zaten [başlık](url) verir;
bu modül onu makine-okunur sources[] + temiz footer'a dönüştürür.
Salt-fonksiyon; dış çağrı yapmaz, sır yazmaz.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List

# [başlık](url) veya çıplak URL yakala
_MD_LINK = re.compile(r'\[([^\]]+)\]\((https?://[^\s)]+)\)')
_BARE_URL = re.compile(r'(?<!\()(https?://[^\s)\]]+)')


def extract_sources(text: str, limit: int = 8) -> List[Dict[str, str]]:
    """Metinden (web_search çıktısı vb.) yapılandırılmış kaynak listesi çıkarır."""
    sources: List[Dict[str, str]] = []
    seen = set()
    for m in _MD_LINK.finditer(text or ""):
        title, url = m.group(1).strip(), m.group(2).strip()
        if url not in seen:
            seen.add(url)
            sources.append({"title": title, "url": url, "domain": _domain(url)})
    if not sources:
        for m in _BARE_URL.finditer(text or ""):
            url = m.group(1).strip().rstrip(".,)")
            if url not in seen:
                seen.add(url)
                sources.append({"title": _domain(url), "url": url, "domain": _domain(url)})
    return sources[:limit]


def _domain(url: str) -> str:
    m = re.match(r'https?://([^/]+)', url or "")
    return (m.group(1) if m else url).replace("www.", "")


def format_sources(sources: List[Dict[str, str]]) -> str:
    """Cevabın altına eklenecek temiz 'Kaynaklar' bölümü (markdown)."""
    if not sources:
        return ""
    lines = ["", "**Kaynaklar:**"]
    for i, s in enumerate(sources, 1):
        lines.append(f"{i}. [{s.get('title') or s.get('domain')}]({s['url']})")
    return "\n".join(lines)


def with_citations(answer: str, search_text: str, limit: int = 6) -> Dict[str, Any]:
    """Bir cevaba kaynakları ekler. Döner: {answer, sources, cited}."""
    sources = extract_sources(search_text, limit=limit)
    cited = answer or ""
    if sources and "Kaynaklar:" not in cited:
        cited = cited.rstrip() + "\n" + format_sources(sources)
    return {"answer": cited, "sources": sources, "cited": bool(sources)}
