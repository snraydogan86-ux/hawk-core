from __future__ import annotations

SHORT_FOLLOWUPS = {
    "başka", "başka?", "devam", "devam et", "detay", "detay ver",
    "sonra", "peki sonra", "daha", "buna göre", "devamını ver",
    "özetle", "özet", "ilk dediğime dön", "ilk konuya dön",
    "ekonomik tarafını anlat", "ekonomik tarafı", "ekonomik kısmı",
    "biraz daha aç", "aç", "uzat", "derinleş", "devamı",
    "another", "continue", "more", "details", "go on", "summarize"
}

def normalize_text(text: str) -> str:
    return (text or "").strip()

def is_short_followup(text: str) -> bool:
    return normalize_text(text).lower() in SHORT_FOLLOWUPS

def build_followup_message(message: str, last_reply: str) -> str:
    raw = normalize_text(message)
    last = normalize_text(last_reply)

    if not raw:
        return raw

    if is_short_followup(raw) and last:
        return (
            "Kullanıcıya az önce şu cevap verildi:\n"
            + last
            + "\n\n"
            + "Kullanıcının yeni mesajı: "
            + raw
            + "\n"
            + "Talimat: Aynı konunun devamını anlat. "
            + "Konu değiştirme. Genel haber özeti verme. "
            + "Önceki cevaptaki başlıkları genişlet. "
            + "Eğer kullanıcı 'ekonomik tarafı' dediyse aynı olayların ekonomik etkisini anlat. "
            + "Eğer kullanıcı 'özetle' dediyse sadece önceki cevabı kısalt. "
            + "Eğer kullanıcı 'detay ver' dediyse aynı cevabı daha somut detaylarla genişlet."
        )

    return raw


def push_turn(history, role, content):
    history = list(history or [])
    item = {
        "role": str(role or "").strip(),
        "content": str(content or "").strip(),
    }
    if item["content"]:
        history.append(item)
    return history[-6:]
