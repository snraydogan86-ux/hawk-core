"""Demo: HAWK's deterministic crisis-safety layer.
Run:  python examples/safety_demo.py
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))
import safety_crisis as sc

MESSAGES = [
    "Merhaba, bugün hava nasıl?",
    "kendimi öldürmek istiyorum",
    "15 yaşındayım ve artık yaşamak istemiyorum",
    "i want to end my life",
]

for m in MESSAGES:
    det = sc.detect(m)
    print(f"\nUSER: {m}")
    print(f"  crisis={det['crisis']} minor={det['minor']} method_seeking={det['method_seeking']}")
    if det["crisis"]:
        print("  HAWK (safe, deterministic):")
        print("   ", sc.safe_response(det, lang="en" if m.isascii() else "tr"))
    else:
        print("  (normal conversation — model answers)")
