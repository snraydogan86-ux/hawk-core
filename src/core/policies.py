def should_force_live_web(message: str) -> bool:
    text = (message or "").strip().lower()

    live_words = [
        "güncel", "bugün", "şu an", "son", "latest", "news", "haber",
        "fiyat", "kur", "kaç tl", "kaç dolar", "ne kadar",
        "bitcoin", "btc", "ethereum", "eth", "dolar", "euro", "altın",
        "hava", "maç", "skor", "sonuç", "cumhurbaşkanı", "başkan",
        "2025", "2026", "2027", "bu yıl", "bu ay", "bu hafta"
    ]
    return any(w in text for w in live_words)
