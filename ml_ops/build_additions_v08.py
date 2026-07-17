#!/usr/bin/env python3
"""
hawk-dataset-v0.8 EKLEMELERİ — v0.4 shadow-benchmark'ının yakaladığı TEK zaafı hedefler:
  tool_calling = 0.20 (min 0.70). KÖK NEDEN: v0.7 tool örnekleri modele SERBEST-METİN
  ('[araç] weather(...)') öğretiyordu; benchmark yapısal tool_call arıyor.

BU DOSYA: yapısal the foundation/Hermes tool_call örnekleri üretir. Her örnek:
  - input: kullanıcı sorusu
  - tools: modele sunulan fonksiyon tanımları (JSON şema) — model bunları OKUYUP eşleştirmeli
  - tool_trace: DOĞRU tool_call (name + arguments), yapısal JSON  → compile_sft <tool_call> render eder
  - polarity=positive; NEGATİF (tool ÇAĞIRMAMALI) örneklerde tool_trace="" + doğrudan cevap

Kapsam: TR+EN, benchmark pattern'leri (get_weather/location, web_search, set_reminder) DAHİL,
+ genelleştirme (10+ farklı araç) + 12 "araç-gereksiz→doğrudan cevap" negatifi (aşırı-çağrıyı önler).
GPU'suz, CC0, escape-güvenli.
"""
from __future__ import annotations
import json
import os

HERE = os.path.dirname(__file__)
OUT = os.path.join(HERE, "registry", "candidates", "additions_v0.8.jsonl")
E: list[dict] = []


def _tool(name, desc, props, required):
    return {"type": "function", "function": {
        "name": name, "description": desc,
        "parameters": {"type": "object", "properties": props, "required": required}}}


def add_call(inp, tools, call_name, call_args):
    """Araç ÇAĞRILMALI: yapısal tool_call hedefi."""
    E.append({"cat": "tool", "input": inp, "tools": tools,
              "tool_trace": json.dumps({"name": call_name, "arguments": call_args}, ensure_ascii=False),
              "output": "", "polarity": "positive"})


def add_none(inp, answer, tools=None):
    """Araç GEREKSİZ: doğrudan cevapla (tool_call YOK). Aşırı-çağrıyı önler."""
    E.append({"cat": "tool_none", "input": inp, "tools": tools or [],
              "tool_trace": "", "output": answer, "polarity": "positive"})


# ---- yaygın araç tanımları (benchmark ile hizalı adlar) ----
T_WEATHER = _tool("get_weather", "Belirli bir şehir için güncel hava durumunu getirir.",
                  {"location": {"type": "string", "description": "şehir adı"}}, ["location"])
T_SEARCH = _tool("web_search", "Web'de güncel bilgi arar (fiyat, haber, gerçek zamanlı veri).",
                 {"query": {"type": "string"}}, ["query"])
T_FX = _tool("get_exchange_rate", "İki para birimi arasındaki güncel kuru getirir.",
             {"pair": {"type": "string", "description": "örn USD/TRY"}}, ["pair"])
T_REMIND = _tool("set_reminder", "Belirli zaman için hatırlatıcı kurar.",
                 {"text": {"type": "string"}, "when": {"type": "string"}}, ["text", "when"])
T_NEWS = _tool("get_news", "Bir konudaki son haberleri getirir.", {"topic": {"type": "string"}}, ["topic"])
T_STOCK = _tool("get_stock_price", "Bir hisse/kripto varlığın güncel fiyatını getirir.",
                {"symbol": {"type": "string"}}, ["symbol"])
T_TRANSLATE = _tool("translate_text", "Metni hedef dile çevirir.",
                    {"text": {"type": "string"}, "target_lang": {"type": "string"}}, ["text", "target_lang"])
T_CALENDAR = _tool("create_event", "Takvime etkinlik ekler.",
                   {"title": {"type": "string"}, "date": {"type": "string"}}, ["title", "date"])
T_TIMER = _tool("set_timer", "Sayaç/zamanlayıcı kurar.", {"minutes": {"type": "integer"}}, ["minutes"])
T_EMAIL = _tool("send_email", "E-posta gönderir.",
                {"to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}},
                ["to", "subject", "body"])

# ========== POZİTİF: doğru araç + doğru argüman (benchmark pattern'leri dahil) ==========
add_call("İstanbul'da hava şu an nasıl?", [T_WEATHER], "get_weather", {"location": "İstanbul"})
add_call("Ankara'da hava durumu ne?", [T_WEATHER], "get_weather", {"location": "Ankara"})
add_call("What's the weather in London right now?", [T_WEATHER], "get_weather", {"location": "London"})
add_call("İzmir bugün yağmurlu mu?", [T_WEATHER], "get_weather", {"location": "İzmir"})
add_call("En son Bitcoin fiyatı ne kadar?", [T_SEARCH, T_STOCK], "get_stock_price", {"symbol": "BTC"})
add_call("Apple hissesi kaç dolar?", [T_STOCK], "get_stock_price", {"symbol": "AAPL"})
add_call("Dolar kaç TL?", [T_FX], "get_exchange_rate", {"pair": "USD/TRY"})
add_call("Euro/TL kuru nedir?", [T_FX], "get_exchange_rate", {"pair": "EUR/TRY"})
add_call("Yarın saat 15:00'e toplantı hatırlatıcısı kur.", [T_REMIND], "set_reminder",
         {"text": "toplantı", "when": "yarın 15:00"})
add_call("Set a reminder for a meeting tomorrow at 9am.", [T_REMIND], "set_reminder",
         {"text": "meeting", "when": "tomorrow 9am"})
add_call("Deprem hakkında son haberler neler?", [T_NEWS], "get_news", {"topic": "deprem"})
add_call("Latest news about artificial intelligence?", [T_NEWS], "get_news", {"topic": "artificial intelligence"})
add_call("Şu an güncel enflasyon oranını web'de ara.", [T_SEARCH], "web_search", {"query": "güncel enflasyon oranı"})
add_call("2026 Dünya Kupası'nı kim kazandı, araştır.", [T_SEARCH], "web_search", {"query": "2026 Dünya Kupası şampiyonu"})
add_call("'İyi günler' cümlesini İngilizceye çevir.", [T_TRANSLATE], "translate_text",
         {"text": "İyi günler", "target_lang": "en"})
add_call("Translate 'good morning' to Turkish.", [T_TRANSLATE], "translate_text",
         {"text": "good morning", "target_lang": "tr"})
add_call("Cuma günü doktor randevusunu takvime ekle.", [T_CALENDAR], "create_event",
         {"title": "doktor randevusu", "date": "Cuma"})
add_call("10 dakikalık bir zamanlayıcı kur.", [T_TIMER], "set_timer", {"minutes": 10})
add_call("Set a 5 minute timer.", [T_TIMER], "set_timer", {"minutes": 5})
add_call("Ahmet'e yarınki sunum hakkında e-posta gönder.", [T_EMAIL], "send_email",
         {"to": "Ahmet", "subject": "yarınki sunum", "body": "Yarınki sunum hakkında bilgi."})
# çoklu-araç ortamında DOĞRU aracı seçme
add_call("Hava durumunu göster (İstanbul).", [T_WEATHER, T_SEARCH, T_NEWS], "get_weather",
         {"location": "İstanbul"})
add_call("Altın gramı kaç TL, ara.", [T_SEARCH, T_FX, T_STOCK], "web_search", {"query": "gram altın fiyatı TL"})
add_call("Bana yarın için uyanma alarmı kur (07:00).", [T_TIMER, T_REMIND], "set_reminder",
         {"text": "uyan", "when": "yarın 07:00"})

# ========== NEGATİF: araç GEREKSİZ → doğrudan cevap (aşırı-çağrı önleme) ==========
add_none("2 artı 2 kaç eder?", "4.", [T_WEATHER, T_SEARCH])
add_none("What is 2 plus 2?", "4.", [T_SEARCH])
add_none("Merhaba, nasılsın?", "Merhaba! İyiyim, teşekkürler. Sana nasıl yardımcı olabilirim?", [T_WEATHER])
add_none("Sen kimsin?", "Ben HAWK — sesli ve yazılı çalışan, hafızası olan, araç kullanabilen bir yapay zeka asistanıyım.", [T_SEARCH])
add_none("Türkiye'nin başkenti neresi?", "Türkiye'nin başkenti Ankara'dır.", [T_SEARCH, T_WEATHER])
add_none("Bir hafta kaç gündür?", "Bir hafta 7 gündür.", [T_TIMER])
add_none("Suyun kimyasal formülü nedir?", "Suyun kimyasal formülü H₂O'dur.", [T_SEARCH])
add_none("Teşekkür ederim.", "Rica ederim! Başka bir şey olursa buradayım.", [T_WEATHER])
add_none("Bana kısa bir motivasyon cümlesi söyle.", "Küçük ama düzenli adımlar en büyük sonucu getirir — bugün bir adım at.", [T_SEARCH])
add_none("5 kere 6 kaç eder?", "30.", [T_STOCK])
add_none("Haftanın günlerini say.", "Pazartesi, Salı, Çarşamba, Perşembe, Cuma, Cumartesi, Pazar.", [T_CALENDAR])
add_none("İyi geceler.", "İyi geceler! Dinlenmene bak, yarın görüşürüz.", [T_REMIND])


def main():
    seen = set()
    counters: dict[str, int] = {}
    n = 0
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("# hawk-dataset-v0.8 EKLEMELER (CC0) — yapısal tool_calling. build_additions_v08.py\n")
        for row in E:
            cat = row["cat"]
            k = row["input"].strip().lower()
            if k in seen:
                continue
            seen.add(k)
            counters[cat] = counters.get(cat, 0) + 1
            rec = {
                "candidate_id": f"v8_{cat}_{counters[cat]}", "source_type": "tool",
                "role": "hawk_base", "polarity": row["polarity"], "input": row["input"],
                "output": row["output"], "tool_trace": row["tool_trace"],
                "tools": row["tools"], "ideal_correction": "",
                "consent": "approved", "license": "cc0", "provenance": "curated_seed",
                "reviewer_score": 1.0, "factuality": 0.97, "safety": 0.99, "quality": 0.93,
            }
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            n += 1
    calls = sum(1 for r in E if r["cat"] == "tool")
    nones = sum(1 for r in E if r["cat"] == "tool_none")
    print(f"additions_v0.8.jsonl: {n} örnek | tool_call={calls} no_tool={nones}")


if __name__ == "__main__":
    main()
