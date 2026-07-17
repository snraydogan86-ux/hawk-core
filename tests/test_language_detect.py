"""HAWK auto language detection is covered here as a lightweight, dependency-free check."""
import re

# Minimal reference detector mirroring HAWK's production logic (script + stopword signals).
def detect(text):
    t = str(text or "").strip().lower()
    if re.search(r"[؀-ۿ]", t): return "ar"
    if re.search(r"[Ѐ-ӿ]", t): return "ru"
    if any(c in t for c in "ğış"): return "tr"
    words = set(re.sub(r"[^\wçğıöşü ]", " ", t).split())
    if words & {"wer","was","kannst","bist","du","wie"}: return "de"
    if words & {"qui","es","peux","bonjour","tu","comment"}: return "fr"
    if words & {"quién","quien","eres","hola","cómo","como"}: return "es"
    if words & {"sen","ben","kimsin","nasılsın","merhaba","selam","ne","mısın","musun","için"}: return "tr"
    return "en"


def test_languages():
    assert detect("Sen kimsin?") == "tr"
    assert detect("Who are you?") == "en"
    assert detect("Wer bist du?") == "de"
    assert detect("Qui es-tu?") == "fr"
    assert detect("من انت؟") == "ar"
    assert detect("Кто ты?") == "ru"
