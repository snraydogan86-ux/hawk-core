"""
SFT derleyici (TORCH-FREE) — frozen governed dataset → the foundation ChatML eğitim dosyası.

Governed JSONL (registry/candidates/*.jsonl) yalnız KABUL edilmiş, redakte, consent'li
adaylar içerir. Bu derleyici onları HAWK sistem-persona'sıyla the foundation ChatML formatına
render eder ve train/val'e böler. GPU/torch GEREKMEZ — çıktı, LoRA trainer'ın (GPU kutusu)
doğrudan tükettiği dosyadır. Determinist: aynı girdi → aynı çıktı (seed'li split).

Kullanım:
    python -m core.model_family.compile_sft \
        --in core/model_family/registry/candidates/hawk-dataset-v0.2.jsonl \
        --out core/model_family/registry/sft/hawk_base_v0.2.sft.jsonl
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os

# HAWK Base persona — eğitimde modelin BENİMSEYECEĞİ kimlik (kısa, tutarlı).
# core/identity.py ile aynı ruh: HAWK kim, ne yapar; kurucu adı yalnız açıkça sorulunca.
HAWK_SYSTEM = (
    "Sen HAWK'sın — sesli ve yazılı çalışan, kalıcı hafızası olan, araç kullanabilen "
    "bir yapay zeka asistanısın. Web, mobil ve masaüstünde çalışırsın; Türkçe ve çok "
    "dilli yanıt verirsin. Kısa, net, samimi ve dürüst konuşursun; bilmediğinde uydurmazsın. "
    "Kişisel veriyi (TC, telefon, kart, IBAN, e-posta, sır) ifşa etmez, güvenliği önemsersin."
)

IM_START = "<|im_start|>"
IM_END = "<|im_end|>"


def _render_chatml(system: str, user: str, assistant: str) -> str:
    """the foundation/ChatML formatı (tokenizer'sız, saf metin — trainer aynı şablonu kullanır)."""
    return (
        f"{IM_START}system\n{system}{IM_END}\n"
        f"{IM_START}user\n{user}{IM_END}\n"
        f"{IM_START}assistant\n{assistant}{IM_END}\n"
    )


# the foundation/Hermes tool-call formatı — train/serve AYNI olmalı (hawk_lora_server.py ile senkron).
TOOLS_HEADER = (
    "\n\nKullanabileceğin araçlar (yalnız gerekliyse çağır, gereksizse doğrudan yanıtla):\n"
    "<tools>\n{tools_json}\n</tools>\n"
    "Bir araç gerekiyorsa TAM olarak şu formatta çağır: "
    "<tool_call>{{\"name\": \"...\", \"arguments\": {{...}}}}</tool_call>"
)


def _system_with_tools(system: str, tools: list) -> str:
    """Araç tanımlarını sistem-prompt'una ekle (model tools'u OKUYUP eşleştirmeyi öğrensin → genelleşir)."""
    if not tools:
        return system
    try:
        tj = json.dumps(tools, ensure_ascii=False)
    except Exception:
        return system
    return system + TOOLS_HEADER.format(tools_json=tj)


def _target_output(row: dict) -> str | None:
    """Eğitim hedefi: positive → output/tool_call; negative → ideal_correction (yoksa atla)."""
    pol = str(row.get("polarity", "positive"))
    out = str(row.get("output", "") or "").strip()
    tool = str(row.get("tool_trace", "") or "").strip()
    if pol == "negative":
        corr = str(row.get("ideal_correction", "") or "").strip()
        return corr or None
    # YAPISAL tool_call (v0.8+): tool_trace = {"name":..., "arguments":{...}} → <tool_call> render.
    if tool:
        try:
            obj = json.loads(tool)
            if isinstance(obj, dict) and obj.get("name"):
                block = "<tool_call>" + json.dumps(obj, ensure_ascii=False) + "</tool_call>"
                return (out + "\n" + block).strip() if out else block
        except Exception:
            # legacy (v0.7) serbest-metin tool izi → geriye dönük uyum
            return f"{out}\n\n[araç]\n{tool}" if out else f"[araç]\n{tool}"
    if not out:
        return None
    return out


def compile_sft(in_path: str, out_path: str, val_ratio: float = 0.15,
                system_prompt: str = HAWK_SYSTEM) -> dict:
    rows = [json.loads(l) for l in open(in_path, encoding="utf-8") if l.strip()]
    examples = []
    skipped = 0
    for r in rows:
        tgt = _target_output(r)
        user = str(r.get("input", "") or "").strip()
        if not tgt or not user:
            skipped += 1
            continue
        # v0.8+: araç örneklerinde tool tanımlarını sistem-prompt'una göm (model tools'u okusun).
        sys_row = _system_with_tools(system_prompt, r.get("tools") or [])
        # v0.9: çok-turlu bağlam (history) → son user/assistant'tan ÖNCE eklenir (çok-turlu hafıza).
        msgs = [{"role": "system", "content": sys_row}]
        for h in (r.get("history") or []):
            hr = str(h.get("role", "")).strip()
            hc = str(h.get("content", "")).strip()
            if hr in ("user", "assistant") and hc:
                msgs.append({"role": hr, "content": hc})
        msgs.append({"role": "user", "content": user})
        msgs.append({"role": "assistant", "content": tgt})
        text = "".join(f"{IM_START}{m['role']}\n{m['content']}{IM_END}\n" for m in msgs)
        examples.append({
            "candidate_id": r.get("candidate_id", ""),
            "role": r.get("role", ""),
            "source_type": r.get("source_type", ""),
            "messages": msgs,
            "text": text,
        })

    # Determinist split: candidate_id hash'ine göre (rastgelelik yok → tekrarlanabilir)
    def _bucket(cid: str) -> float:
        h = int(hashlib.sha256(cid.encode()).hexdigest()[:8], 16)
        return (h % 10000) / 10000.0

    train, val = [], []
    for ex in examples:
        (val if _bucket(ex["candidate_id"]) < val_ratio else train).append(ex)

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        for ex in train:
            f.write(json.dumps(ex, ensure_ascii=False) + "\n")
    val_path = out_path.replace(".jsonl", ".val.jsonl")
    with open(val_path, "w", encoding="utf-8") as f:
        for ex in val:
            f.write(json.dumps(ex, ensure_ascii=False) + "\n")

    # kaba token tahmini (~4 char/token) ve uzunluk istatistiği
    lens = [len(ex["text"]) for ex in examples]
    stats = {
        "in_rows": len(rows),
        "compiled": len(examples),
        "skipped": skipped,
        "train": len(train),
        "val": len(val),
        "avg_chars": round(sum(lens) / max(1, len(lens))),
        "max_chars": max(lens) if lens else 0,
        "est_tokens_total": round(sum(lens) / 4),
        "out": out_path,
        "val_out": val_path,
    }
    return stats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="in_path", required=True)
    ap.add_argument("--out", dest="out_path", required=True)
    ap.add_argument("--val-ratio", type=float, default=0.15)
    a = ap.parse_args()
    st = compile_sft(a.in_path, a.out_path, a.val_ratio)
    print("=== HAWK Base SFT derlendi (torch-free) ===")
    for k, v in st.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
