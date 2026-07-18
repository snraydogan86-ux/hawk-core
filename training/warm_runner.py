#!/usr/bin/env python3
"""
WARM POD beyni — GPU pod'unda çalışır, AYAKTA kalır. Kontrol dosyasını (ml_control.txt,
public repo) yoklar; token 'run-N' olunca trainer+veriyi TAZE çeker (fix'ler uygulanır)
ve eğitimi çalıştırır. Model /workspace/hf'ye BİR KEZ iner (volume cache) → re-run hızlı.

Durum/loglar /workspace'e yazılır, http.server:8000 ile proxy'den okunur:
  status.txt: warm-idle | training run-N | done-ok run-N | done-fail run-N | stopped
  train.log:  eğitim çıktısı + TRAIN_EXIT + ADAPTER_OK/MISSING
  adapter.tgz: başarıda eğitilen LoRA adapter
"""
import os, time, subprocess, urllib.request

# FIX: HAWK-AI private olunca eski public-main raw 404 oldu. KOD public hawk-core'dan çekilir;
# eğitim VERİSİ (vendor-adlı → public repo'ya konmaz) R2 presigned URL ile env'den gelir.
RAW = os.getenv("HAWK_TRAIN_RAW_BASE",
                "https://raw.githubusercontent.com/snraydogan86-ux/hawk-core/main")
WS = "/workspace"
FILES = {
    "train.py": f"{RAW}/training/train_hawk_base_lora.py",
    # data/val: R2 presigned (launcher env ile geçer); yoksa hawk-core örnek verisine düşer.
    "data.jsonl": os.getenv("HAWK_DATA_URL") or f"{RAW}/training/data_sample.jsonl",
    "val.jsonl": os.getenv("HAWK_VAL_URL") or f"{RAW}/training/data_sample.jsonl",
}
BASE = os.getenv("HAWK_TRAIN_BASE", "Qwen/Qwen3-8B")
EPOCHS = os.getenv("HAWK_TRAIN_EPOCHS", "3")
_HDR = {"Cache-Control": "no-cache", "Pragma": "no-cache", "User-Agent": "Mozilla/5.0"}


def _get(url, timeout=60):
    req = urllib.request.Request(url, headers=_HDR)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def fetch(url, dst):
    open(dst, "wb").write(_get(url))


def ctrl_token():
    try:
        return _get(os.getenv("HAWK_CTRL_URL") or f"{RAW}/training/ml_control.txt", timeout=20).decode().strip()
    except Exception:
        return ""


def setstatus(s):
    open(f"{WS}/status.txt", "w").write(s + "\n")


def run_training(token):
    setstatus(f"training {token}")
    for name, url in FILES.items():        # TAZE çek → push'ladığım fix'ler uygulanır
        fetch(url, f"{WS}/{name}")
    env = dict(os.environ, HF_HUB_ENABLE_HF_TRANSFER="1", HF_HOME=f"{WS}/hf")
    with open(f"{WS}/train.log", "w") as lg:
        rc = subprocess.call(
            ["python", f"{WS}/train.py", "--base", BASE, "--train", f"{WS}/data.jsonl",
             "--val", f"{WS}/val.jsonl", "--out", f"{WS}/adapter-hawk-base",
             "--qlora", "--epochs", EPOCHS],
            stdout=lg, stderr=subprocess.STDOUT, env=env)
        lg.write(f"\nTRAIN_EXIT={rc}\n")
    ok = rc == 0 and os.path.isdir(f"{WS}/adapter-hawk-base")
    if ok:
        subprocess.call(["tar", "czf", f"{WS}/adapter.tgz", "-C", WS, "adapter-hawk-base"])
        open(f"{WS}/train.log", "a").write("ADAPTER_OK\n")
        setstatus(f"done-ok {token}")
    else:
        open(f"{WS}/train.log", "a").write("ADAPTER_MISSING\n")
        setstatus(f"done-fail {token}")


def main():
    os.chdir(WS)
    last = None
    setstatus("warm-idle")
    while True:
        c = ctrl_token()
        if c.startswith("run") and c != last:
            last = c
            try:
                run_training(c)
            except Exception as e:
                open(f"{WS}/train.log", "a").write(f"\nRUNNER_ERROR: {e}\n")
                setstatus(f"error {c}")
        elif c == "stop":
            setstatus("stopped")
            break
        time.sleep(20)


if __name__ == "__main__":
    main()
