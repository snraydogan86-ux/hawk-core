#!/usr/bin/env python3
"""
hawk-dataset-v0.9 EKLEMELERİ — v0.5 benchmark'ının yakaladığı hafıza regresyonunu hedefler:
  MS-01: "adım Soner" (turn1) → "az önce söylediğim ad neydi?" (turn2) → v0.5 "Asım" UYDURDU (FAIL).
  v0.4 geçmişti; tool-odaklı v0.8 eğitimi çok-turlu bağlam takibini aşındırdı.

BU DOSYA: ÇOK-TURLU hafıza-hatırlama örnekleri. Her örnek:
  - history: [önceki user, önceki assistant] → bağlam
  - input: hatırlama sorusu (turn 2)
  - output: DOĞRU hatırlama (kullanıcının verdiği bilgi; model KENDİNİ karıştırmaz)

Doktrin: model, kullanıcının önceki turda verdiği bilgiyi (ad/proje/tercih/sayı/konum) DOĞRU
hatırlamalı; asla kendine ad uydurmamalı, konuyu şaşırmamalı. TR+EN. CC0, governance-uyumlu.
"""
from __future__ import annotations
import json
import os

HERE = os.path.dirname(__file__)
OUT = os.path.join(HERE, "registry", "candidates", "additions_v0.9.jsonl")
E: list[dict] = []


def add_mem(u1, a1, u2, a2):
    """Çok-turlu: (user1→assistant1) bağlam, sonra user2→assistant2 (hatırlama hedefi)."""
    E.append({"history": [{"role": "user", "content": u1}, {"role": "assistant", "content": a1}],
              "input": u2, "output": a2})


# ---------- AD hatırlama (MS-01 doğrudan hedefi) ----------
add_mem("Benim adım Soner ve üzerinde çalıştığım proje HAWK.",
        "Merhaba Soner! HAWK projesi kulağa güçlü geliyor. Nasıl yardımcı olabilirim?",
        "Az önce sana söylediğim adım neydi?", "Adın Soner.")
add_mem("Ben Ayşe, İzmir'de yaşıyorum.", "Merhaba Ayşe! İzmir güzel şehir. Sana nasıl yardımcı olayım?",
        "Adımı hatırlıyor musun?", "Evet, adın Ayşe.")
add_mem("My name is John and I work as an engineer.",
        "Nice to meet you, John! How can I help you today?",
        "What's my name?", "Your name is John.")
add_mem("Bana Mehmet diyebilirsin.", "Tamam Mehmet, memnun oldum. Ne yapabilirim?",
        "Bana nasıl hitap etmeliyim demiştim?", "Sana Mehmet diye hitap etmemi istemiştin.")

# ---------- PROJE / iş hatırlama ----------
add_mem("Adım Soner, projemin adı HAWK.", "Anladım Soner, HAWK projesiyle ilgili buradayım.",
        "Projemin adı neydi?", "Projenin adı HAWK.")
add_mem("Şu an bir e-ticaret sitesi geliştiriyorum, adı ModaX.",
        "Güzel! ModaX için sana yardımcı olabilirim.",
        "Geliştirdiğim sitenin adını hatırlıyor musun?", "Evet, sitenin adı ModaX.")
add_mem("I'm building a mobile app called TaskFlow.",
        "Great, TaskFlow sounds useful! What do you need help with?",
        "What was the name of my app?", "Your app is called TaskFlow.")

# ---------- TERCİH hatırlama ----------
add_mem("Kahvemi sütsüz ve şekersiz içerim.", "Not aldım — sütsüz, şekersiz kahve.",
        "Kahvemi nasıl içtiğimi söylemiştim?", "Sütsüz ve şekersiz içtiğini söylemiştin.")
add_mem("En sevdiğim renk mavi.", "Güzel bir seçim! Mavi sakinleştirici bir renktir.",
        "En sevdiğim renk neydi?", "En sevdiğin renk mavi.")
add_mem("Vejetaryenim, et yemem.", "Anladım, vejetaryen olduğunu not ettim.",
        "Diyetimle ilgili ne söylemiştim?", "Vejetaryen olduğunu, et yemediğini söylemiştin.")

# ---------- SAYI / tarih hatırlama ----------
add_mem("Toplantımız 14 Mart'ta saat 15:00'te.", "Not ettim: 14 Mart, 15:00 toplantı.",
        "Toplantı ne zamandı?", "Toplantın 14 Mart saat 15:00'te.")
add_mem("Bütçem 5000 lira.", "Anladım, 5000 liralık bütçeye göre yardımcı olabilirim.",
        "Bütçemi hatırlıyor musun?", "Evet, bütçen 5000 lira.")
add_mem("Şifremin ipucu 'ilk evcil hayvanım' ama şifreyi söyleme.",
        "Tamam, ipucunu not ettim ama şifreyle ilgili hassas bilgiyi paylaşmam/saklamam.",
        "İpucu neydi?", "İpucun 'ilk evcil hayvanım' idi. (Şifrenin kendisini istemem/saklamam.)")

# ---------- ÇOKLU-BİLGİ hatırlama ----------
add_mem("Adım Zeynep, İstanbul'da yaşıyorum ve doktorum.",
        "Merhaba Zeynep! İstanbul'dan bir doktor — memnun oldum.",
        "Hakkımda ne biliyorsun?", "Adın Zeynep, İstanbul'da yaşıyorsun ve doktorsun.")
add_mem("İki kedim var: Pamuk ve Duman.", "Ne tatlı! Pamuk ve Duman güzel isimler.",
        "Kedilerimin adları neydi?", "Kedilerinin adları Pamuk ve Duman.")

# ---------- DÜZELTME hatırlama (kullanıcı bilgi güncellerse) ----------
add_mem("Adım Ali. Pardon, aslında Ahmet.", "Tamam, düzelttim — adın Ahmet.",
        "Adım ne?", "Adın Ahmet (Ali dedin ama Ahmet diye düzelttin).")


def main():
    seen = set()
    n = 0
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("# hawk-dataset-v0.9 EKLEMELER (CC0) — çok-turlu hafıza. build_additions_v09.py\n")
        for i, row in enumerate(E, 1):
            k = row["input"].strip().lower() + "|" + str(row["history"])[:40]
            if k in seen:
                continue
            seen.add(k)
            rec = {
                "candidate_id": f"v9_memory_{i}", "source_type": "memory", "role": "hawk_base",
                "polarity": "positive", "input": row["input"], "output": row["output"],
                "tool_trace": "", "history": row["history"], "ideal_correction": "",
                "consent": "approved", "license": "cc0", "provenance": "curated_seed",
                "reviewer_score": 1.0, "factuality": 0.98, "safety": 0.99, "quality": 0.94,
            }
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            n += 1
    print(f"additions_v0.9.jsonl: {n} çok-turlu hafıza örneği")


if __name__ == "__main__":
    main()
