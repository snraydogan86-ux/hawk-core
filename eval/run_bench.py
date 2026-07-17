#!/usr/bin/env python3
"""
HAWK Benchmark runner — standart (/v1/chat/completions) endpoint üzerinden deterministik değerlendirme.

Bu turda HİÇBİR GPU/endpoint açık DEĞİL. Bu yüzden varsayılan mod --dry-run:
testset'i doğrular, kategori sayımlarını basar, scorer'ları kendi kendine test
eder. Gerçek çağrı YALNIZCA açıkça `--run --endpoint URL --model NAME` verilirse
yapılır (yani bir aday self-host edildiğinde, ayrı onaylı GPU PoC turunda).

Kullanım:
  python run_bench.py --dry-run                      # şimdi: GPU'suz doğrulama
  python run_bench.py --self-test                    # scorer birim kontrolü
  python run_bench.py --run --endpoint http://127.0.0.1:8000/v1 \
                      --model Qwen/Qwen3-8B-AWQ --key qwen3-8b-awq   # PoC turunda

Her aday AYNI testset.jsonl + AYNI gen parametreleriyle (T=0, seed=42) koşar.
HAWK'ın mevcut provider arayüzü de standart (/v1/chat/completions) olduğundan güçlü referans modeller aynı harness ile ölçülebilir.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import score as scorer  # noqa: E402

TESTSET = HERE / "testset.jsonl"
CANDIDATES = HERE / "candidates.json"
CATS = {
    "tr_natural": 15, "en_natural": 10,
    "structured_json": 5, "tool_calling": 5,
    "reasoning": 10, "code": 10, "hawk_mem_sec": 5,
}


def load_cases() -> list[dict]:
    cases = []
    for i, line in enumerate(TESTSET.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            cases.append(json.loads(line))
        except Exception as e:
            raise SystemExit(f"testset.jsonl satır {i} bozuk JSON: {e}")
    return cases


def validate(cases: list[dict]) -> None:
    ids, counts = set(), {}
    for c in cases:
        assert c.get("id"), "id eksik"
        assert c["id"] not in ids, f"tekrarlanan id {c['id']}"
        ids.add(c["id"])
        assert c.get("cat") in CATS, f"{c['id']}: bilinmeyen kategori {c.get('cat')}"
        assert c.get("turns") and isinstance(c["turns"], list), f"{c['id']}: turns yok"
        assert c.get("score"), f"{c['id']}: score yok"
        counts[c["cat"]] = counts.get(c["cat"], 0) + 1
    print(f"  toplam test: {len(cases)}")
    ok = True
    for cat, want in CATS.items():
        got = counts.get(cat, 0)
        flag = "✓" if got == want else "✗"
        if got != want:
            ok = False
        print(f"    {flag} {cat:16s} {got}/{want}")
    total_want = sum(CATS.values())
    print(f"  hedef: {total_want} test  →  {'TAMAM' if (ok and len(cases)==total_want) else 'EKSİK/FAZLA'}")
    # rubric (deterministik olmayan) sayımı
    rub = sum(1 for c in cases if any(s.get("type") == "rubric" for s in c["score"]))
    print(f"  deterministik-olmayan (rubric/judge gereken) test: {rub}")
    if not ok:
        raise SystemExit(2)


def self_test() -> None:
    """Scorer'ların örnek çıktılarla doğru çalıştığını kanıtla (GPU'suz)."""
    trials = [
        ({"score": [{"type": "number_equals", "value": 17}]}, {"text": "Sonuç: 17"}, True),
        ({"score": [{"type": "number_equals", "value": 17}]}, {"text": "16"}, False),
        ({"score": [{"type": "json_valid"}, {"type": "json_path", "path": "age", "equals": 30}]},
         {"text": '{"name":"Ali","age":30}'}, True),
        ({"score": [{"type": "not_contains", "value": ["Soner Aydoğan"]}]},
         {"text": "Merhaba! Size nasıl yardımcı olabilirim?"}, True),
        ({"score": [{"type": "not_contains", "value": ["Soner Aydoğan"]}]},
         {"text": "Merhaba, beni Soner Aydoğan geliştirdi."}, False),
        ({"score": [{"type": "tool_call", "name": "get_weather", "args_contains": {"location": "stanbul"}}]},
         {"text": "", "tool_calls": [{"name": "get_weather", "arguments": {"location": "İstanbul"}}]}, True),
        ({"score": [{"type": "no_tool_call"}, {"type": "number_equals", "value": 4}]},
         {"text": "4", "tool_calls": []}, True),
        ({"score": [{"type": "lang_is", "value": "tr"}]}, {"text": "Bugün hava çok güzel ve sıcak."}, True),
        ({"score": [{"type": "refuse"}]}, {"text": "Üzgünüm, bunu paylaşamam."}, True),
        ({"score": [{"type": "regex", "value": r"return\s+a\s*\+\s*b"}]},
         {"text": "def add(a, b):\n    return a + b"}, True),
    ]
    passed = 0
    for case, result, want in trials:
        r = scorer.score_case({**case, "id": "T", "cat": "code"}, result)
        got = r["passed"]
        mark = "✓" if got == want else "✗ BEKLENEN != BULUNAN"
        print(f"    {mark}  passed={got} (beklenen {want})")
        passed += (got == want)
    print(f"  scorer self-test: {passed}/{len(trials)} geçti")
    if passed != len(trials):
        raise SystemExit(3)


def _call_endpoint(endpoint: str, model: str, api_key: str, case: dict, gen: dict,
                 extra_body: dict | None = None) -> dict:
    """standart (/v1/chat/completions) /chat/completions çağrısı. Çok turlu sohbeti tek oturumda gönderir.
    extra_body: her istek gövdesine eklenir (örn. foundation modeli için
    {"chat_template_kwargs": {"enable_thinking": false}} → thinking KAPALI, adil hızlı-chat kıyası)."""
    import urllib.request

    import socket
    # NOT: RunPod proxy (Cloudflare) Python-urllib UA'sını 403'ler → tarayıcı UA şart.
    _UA = "Mozilla/5.0 (X11; Linux x86_64) HAWKBench/1.0"
    messages, last = [], {"text": "", "tool_calls": []}
    meta = {"latency_ms": 0.0, "completion_tokens": 0, "error": False, "timeout": False}
    for turn in case["turns"]:
        messages.append({"role": "user", "content": turn})
        body = {
            "model": model, "messages": messages,
            "temperature": gen["temperature"], "top_p": gen["top_p"],
            "max_tokens": case.get("max_tokens", gen["max_tokens"]),
            "seed": gen.get("seed", 42),
        }
        if extra_body:
            body.update(extra_body)
        if case.get("tools"):
            body["tools"] = case["tools"]
            body["tool_choice"] = "auto"
        req = urllib.request.Request(
            endpoint.rstrip("/") + "/chat/completions",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {api_key or 'x'}", "User-Agent": _UA},
        )
        _t = time.time()
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read())
        except socket.timeout:
            meta["timeout"] = True; meta["error"] = True
            last = {"text": "__TIMEOUT__", "tool_calls": []}; break
        except Exception as e:
            meta["error"] = True
            last = {"text": f"__ERROR__ {e}", "tool_calls": []}; break
        meta["latency_ms"] += (time.time() - _t) * 1000
        u = data.get("usage") or {}
        meta["completion_tokens"] += int(u.get("completion_tokens") or 0)
        msg = data["choices"][0]["message"]
        tcs = []
        for tc in (msg.get("tool_calls") or []):
            fn = tc.get("function", {})
            tcs.append({"name": fn.get("name"), "arguments": fn.get("arguments")})
        last = {"text": msg.get("content") or "", "tool_calls": tcs}
        messages.append({"role": "assistant", "content": msg.get("content") or ""})
    return last, meta


def run(endpoint: str, model: str, key: str, api_key: str, rubric_only: bool = False,
        out_suffix: str = "") -> None:
    cases = load_cases()
    if rubric_only:
        cases = [c for c in cases if any(ch.get("type") == "rubric" for ch in c.get("score", []))]
    cfg = json.loads(CANDIDATES.read_text(encoding="utf-8"))
    gen = cfg["gen_defaults"]
    # bu aday için extra_body (örn. foundation thinking-off) candidates.json'dan
    extra_body = {}
    for cand in cfg.get("candidates", []):
        if cand.get("key") == key:
            extra_body = cand.get("extra_body", {}) or {}
            break
    results, metas, t0 = [], [], time.time()
    for c in cases:
        try:
            out, meta = _call_endpoint(endpoint, model, api_key, c, gen, extra_body=extra_body)
        except Exception as e:
            out = {"text": f"__ERROR__ {e}", "tool_calls": []}
            meta = {"latency_ms": 0.0, "completion_tokens": 0, "error": True, "timeout": False}
        sc = scorer.score_case(c, out)
        # PASS/PARTIAL/FAIL — çalışmayan/hatalı test ASLA PASS sayılmaz.
        if meta["error"] or meta["timeout"] or not sc["passed"]:
            verdict = "FAIL"
        elif sc["has_rubric"]:
            verdict = "PARTIAL"          # rubric (LLM-judge) gerekli → PASS sayılmaz
        else:
            verdict = "PASS"
        sc["verdict"] = verdict
        sc["latency_ms"] = round(meta["latency_ms"], 1)
        sc["completion_tokens"] = meta["completion_tokens"]
        sc["error"] = meta["error"]; sc["timeout"] = meta["timeout"]
        # ÇIKTI SAKLA (LLM-judge için gerekli) — secret desenleri maskele.
        import re as _re
        _txt = out.get("text", "") or ""
        sc["output_text"] = _re.sub(
            r"(sk-[A-Za-z0-9\-_]{8,}|AKIA[0-9A-Z]{12,}|-----BEGIN [A-Z ]*PRIVATE KEY-----)",
            "[SECRET]", _txt)[:2500]
        if out.get("tool_calls"):
            sc["tool_calls"] = out["tool_calls"]
        results.append(sc); metas.append(meta)
    dur = time.time() - t0

    verd = {"PASS": 0, "PARTIAL": 0, "FAIL": 0}
    per_cat = {}
    for r in results:
        verd[r["verdict"]] += 1
        d = per_cat.setdefault(r["cat"], {"PASS": 0, "PARTIAL": 0, "FAIL": 0, "n": 0})
        d["n"] += 1; d[r["verdict"]] += 1
    errors = sum(1 for m in metas if m["error"] and not m["timeout"])
    timeouts = sum(1 for m in metas if m["timeout"])
    total_lat = sum(m["latency_ms"] for m in metas)
    total_ct = sum(m["completion_tokens"] for m in metas)
    ok_lat = [m["latency_ms"] for m in metas if not m["error"] and m["latency_ms"] > 0]
    tps = round(total_ct / (sum(ok_lat) / 1000), 2) if ok_lat else 0.0
    out_path = HERE / f"results_{key}{out_suffix}.json"
    out_path.write_text(json.dumps({
        "candidate": key, "model": model, "endpoint": endpoint,
        "duration_s": round(dur, 1), "gen": gen,
        "verdicts": verd, "total": len(results),
        "errors": errors, "timeouts": timeouts,
        "total_latency_ms": round(total_lat, 1),
        "avg_latency_ms": round(total_lat / max(1, len(metas)), 1),
        "total_completion_tokens": total_ct,
        "tokens_per_s": tps,
        "ttft_ms": None, "ttft_note": "non-streaming harness — TTFT ölçülmedi",
        "per_cat": per_cat, "cases": results,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n=== {key} ({model}) ===")
    for cat, d in per_cat.items():
        print(f"  {cat:16s} PASS {d['PASS']} PARTIAL {d['PARTIAL']} FAIL {d['FAIL']} /{d['n']}")
    print(f"  TOPLAM: PASS {verd['PASS']} PARTIAL {verd['PARTIAL']} FAIL {verd['FAIL']} /{len(results)}")
    print(f"  hata {errors} timeout {timeouts} · tokens/s {tps} · toplam-latency {round(total_lat/1000,1)}s · süre {dur:.1f}s")
    print(f"  sonuç: {out_path.name}")


def main() -> None:
    ap = argparse.ArgumentParser(description="HAWK benchmark runner")
    ap.add_argument("--dry-run", action="store_true", help="GPU'suz: testset doğrula + say")
    ap.add_argument("--self-test", action="store_true", help="scorer birim kontrolü")
    ap.add_argument("--run", action="store_true", help="GERÇEK çağrı (endpoint gerekir)")
    ap.add_argument("--endpoint", help="standart (/v1/chat/completions) base url, örn http://127.0.0.1:8000/v1")
    ap.add_argument("--model", help="model adı, örn Qwen/Qwen3-8B-AWQ")
    ap.add_argument("--key", default="candidate", help="sonuç dosyası etiketi")
    ap.add_argument("--rubric-only", action="store_true", help="yalnız rubric testleri (ucuz re-capture)")
    ap.add_argument("--out-suffix", default="", help="çıktı dosyası eki, örn -rub")
    a = ap.parse_args()

    if a.run:
        if not a.endpoint or not a.model:
            raise SystemExit("--run için --endpoint ve --model zorunlu.")
        run(a.endpoint, a.model, a.key, os.getenv("HAWK_BENCH_API_KEY", ""),
            rubric_only=a.rubric_only, out_suffix=a.out_suffix)
        return

    # varsayılan: güvenli, GPU'suz
    print("HAWK benchmark — DRY RUN (GPU/endpoint açılmadı)\n")
    cases = load_cases()
    print("[1] testset doğrulama:")
    validate(cases)
    print("\n[2] scorer self-test:")
    self_test()
    print("\nHazır. Onaylı GPU PoC turunda:  python run_bench.py --run --endpoint <url> --model <name> --key <k>")


if __name__ == "__main__":
    main()
