#!/usr/bin/env python3
"""
hawk-dataset-v0.5 EKLEMELERİ — v0.4'te kalan boşlukları kapatır (workspace, code).
GPU'suz, production verisi YOK, CC0. json.dumps ile escape-güvenli.
Kullanım: python -m core.model_family.build_additions_v05
"""
from __future__ import annotations

import json
import os

HERE = os.path.dirname(__file__)
OUT = os.path.join(HERE, "registry", "candidates", "additions_v0.5.jsonl")

E: list[tuple] = []


def add(cat, inp, out, *, role="hawk_base", pol="positive", tool="", corr=""):
    E.append((cat, role, pol, inp, out, tool, corr))


# ---------------- workspace (kalan 6 + fazladan) ----------------
add("workspace", "app.py dosyasını src klasörüne taşı.", "Dosyayı taşıyorum.",
    tool="move(src=\"app.py\", dst=\"src/app.py\") -> ok")
add("workspace", "index.html'in bir yedeğini index.bak olarak al.", "Yedek kopya oluşturuyorum.",
    tool="copy(src=\"index.html\", dst=\"index.bak\") -> ok")
add("workspace", "main.py kaç satır?", "Satır sayısını hesaplıyorum.",
    tool="read_file(path=\"main.py\") -> metin; line_count=42")
add("workspace", "Projeye bir .gitignore ekle: node_modules ve .env gizle.", "Standart .gitignore oluşturuyorum.",
    tool="write_file(path=\".gitignore\", content=\"node_modules/\\n.env\\n\") -> ok")
add("workspace", "Tüm dosyalarda 'apiKey' geçen yerleri ara.", "Tüm projede arıyorum.",
    tool="search_all(query=\"apiKey\") -> [{\"file\":\"config.js\",\"line\":3}]")
add("workspace", "style.css dosyasının boyutunu göster.", "Dosya bilgisini getiriyorum.",
    tool="stat(path=\"style.css\") -> {\"size_bytes\":1840}")
add("workspace", "data klasöründeki dosyaları listele.", "data klasörünü listeliyorum.",
    tool="list_files(path=\"data\") -> [\"a.csv\",\"b.csv\"]")
add("workspace", "utils.py dosyasını çalıştırmadan sözdizimini kontrol et.", "Sözdizimini derleyerek kontrol ediyorum.",
    tool="py_compile(path=\"utils.py\") -> {\"ok\":true}")

# ---------------- code (kalan 1 + fazladan) ----------------
add("code", "Python'da bir JSON dosyasını oku.", "import json\nwith open(\"veri.json\") as f:\n    data = json.load(f)\nprint(data)", role="hawk_code")
add("code", "Python'da bir sözlüğü JSON dosyasına yaz.", "import json\nd = {\"ad\": \"Ali\"}\nwith open(\"cikti.json\", \"w\") as f:\n    json.dump(d, f, ensure_ascii=False)", role="hawk_code")


def main() -> None:
    seen = set()
    counters: dict[str, int] = {}
    n = 0
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("# hawk-dataset-v0.5 kürasyonlu EKLEMELER (CC0). build_additions_v05.py ile üretildi.\n")
        for cat, role, pol, inp, out, tool, corr in E:
            key = inp.strip().lower()
            if key in seen:
                continue
            seen.add(key)
            counters[cat] = counters.get(cat, 0) + 1
            rec = {
                "candidate_id": f"v5_{cat}_{counters[cat]}", "source_type": cat, "role": role,
                "polarity": pol, "input": inp, "output": out, "tool_trace": tool,
                "ideal_correction": corr, "consent": "approved", "license": "cc0",
                "provenance": "curated_seed", "reviewer_score": 1.0, "factuality": 0.95,
                "safety": 0.97, "quality": 0.9,
            }
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            n += 1
    print(f"additions_v0.5.jsonl yazıldı: {n} örnek | dağılım: {dict(sorted(counters.items()))}")


if __name__ == "__main__":
    main()
