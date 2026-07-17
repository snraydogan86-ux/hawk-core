def is_general_web_query(text: str) -> bool:
    t = str(text or "").strip().lower()

    triggers = [
        "güncel haberler",
        "guncel haberler",
        "haberler",
        "son haberler",
        "haber",
        "son durum",
        "bugün neler oldu",
        "bugun neler oldu",
        "gündem",
        "gundem",
        "güncel",
        "latest news",
        "news",
    ]

    return any(x in t for x in triggers)


def search_results_look_empty(results):
    if not results:
        return True
    if isinstance(results, list) and len(results) == 0:
        return True
    return False

def build_strict_news_answer(results):
    if not results:
        return "Şu anda güncel haber verisine ulaşılamıyor."

    if isinstance(results, list):
        top = results[:3]
        out = []
        for r in top:
            if isinstance(r, dict):
                title = r.get("title") or ""
                out.append(f"- {title}")
            else:
                out.append(str(r))
        return "\n".join(out)

    return str(results)
