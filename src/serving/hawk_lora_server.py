#!/usr/bin/env python3
"""
HAWK LoRA mini chat-completions uyumlu server (transformers+peft, GPU pod'unda).
the serving engine crash-loop yaptığı için KANITLI stack (eval bununla çalıştı). Yavaş ama güvenilir.
Endpoint: GET /v1/models (hazır sinyali) + POST /v1/chat/completions (chat-completions uyumlu).
enable_thinking=False (v0.3 uyumlu). 4-bit QLoRA -> 24GB'a sığar.
"""
import json, os, re
from http.server import HTTPServer, BaseHTTPRequestHandler

BASE = os.getenv("BASE", "OpenFoundation/Model")
ADAPTER = os.getenv("ADAPTER", "/workspace/ad/adapter-hawk-base")

# v0.8: tool-calling — compile_sft.py TOOLS_HEADER ile AYNI format (train/serve tutarlı).
TOOLS_HEADER = (
    "\n\nKullanabileceğin araçlar (yalnız gerekliyse çağır, gereksizse doğrudan yanıtla):\n"
    "<tools>\n{tools_json}\n</tools>\n"
    "Bir araç gerekiyorsa TAM olarak şu formatta çağır: "
    "<tool_call>{{\"name\": \"...\", \"arguments\": {{...}}}}</tool_call>"
)
_TOOLCALL_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.S)


def _inject_tools(messages, tools):
    """Araç tanımlarını sistem mesajına göm (eğitimdeki ile AYNI blok). tools yoksa değişmez."""
    if not tools:
        return messages
    try:
        block = TOOLS_HEADER.format(tools_json=json.dumps(tools, ensure_ascii=False))
    except Exception:
        return messages
    msgs = [dict(m) for m in messages]
    for m in msgs:
        if m.get("role") == "system":
            m["content"] = str(m.get("content", "")) + block
            return msgs
    return [{"role": "system", "content": "Sen HAWK'sın." + block}] + msgs


def _parse_tool_calls(text):
    """Çıktıdaki <tool_call>{...}</tool_call> bloklarını standart tool_calls[] formatına çevir.
    Döner: (content_without_calls, tool_calls_list). Hiç yoksa (text, [])."""
    calls = []
    for i, m in enumerate(_TOOLCALL_RE.finditer(text or "")):
        try:
            obj = json.loads(m.group(1))
            if isinstance(obj, dict) and obj.get("name"):
                calls.append({"id": f"call_{i}", "type": "function", "function": {
                    "name": obj["name"],
                    "arguments": json.dumps(obj.get("arguments", {}), ensure_ascii=False)}})
        except Exception:
            continue
    content = _TOOLCALL_RE.sub("", text or "").strip()
    return content, calls

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
from peft import PeftModel

print("[server] model yükleniyor...", flush=True)
tok = AutoTokenizer.from_pretrained(BASE, trust_remote_code=True)
if tok.pad_token is None:
    tok.pad_token = tok.eos_token
quant = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                           bnb_4bit_compute_dtype=torch.bfloat16, bnb_4bit_use_double_quant=True)
model = AutoModelForCausalLM.from_pretrained(BASE, quantization_config=quant, trust_remote_code=True,
                                             torch_dtype=torch.bfloat16, device_map="auto")
model = PeftModel.from_pretrained(model, ADAPTER)
model.eval()
print("[server] hazır — hawk-base LoRA yüklü, :8000", flush=True)


def generate(messages, max_new):
    ids = tok.apply_chat_template(messages, return_tensors="pt", add_generation_prompt=True,
                                  enable_thinking=False).to(model.device)
    with torch.no_grad():
        out = model.generate(ids, max_new_tokens=max_new, do_sample=False,
                             pad_token_id=tok.pad_token_id or tok.eos_token_id)
    return tok.decode(out[0][ids.shape[1]:], skip_special_tokens=True).strip()


class H(BaseHTTPRequestHandler):
    def _j(self, o, code=200):
        b = json.dumps(o).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path.startswith("/v1/models"):
            self._j({"object": "list", "data": [{"id": "hawk-base", "object": "model"}]})
        else:
            self._j({"status": "ok"})

    def do_POST(self):
        try:
            ln = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(ln) or b"{}")
            msgs = body.get("messages", [])
            tools = body.get("tools") or []
            msgs = _inject_tools(msgs, tools)   # v0.8: araç tanımlarını prompt'a göm
            txt = generate(msgs, int(body.get("max_tokens", 300)))
            content, tool_calls = _parse_tool_calls(txt)
            message = {"role": "assistant", "content": content or None}
            finish = "stop"
            if tool_calls:
                message["tool_calls"] = tool_calls    # chat-completions uyumlu structured tool_calls
                finish = "tool_calls"
            self._j({"object": "chat.completion", "model": "hawk-base",
                     "choices": [{"index": 0, "message": message, "finish_reason": finish}]})
        except Exception as e:
            self._j({"error": str(e)[:200]}, 500)

    def log_message(self, *a):
        pass


HTTPServer(("0.0.0.0", 8000), H).serve_forever()
