"""Tests for HAWK's deterministic crisis-safety layer."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "core"))
import safety_crisis as sc


def test_detects_self_harm_intent():
    assert sc.detect("kendimi öldürmek istiyorum")["crisis"]
    assert sc.detect("i want to kill myself")["crisis"]
    assert sc.detect("artık yaşamak istemiyorum")["crisis"]


def test_does_not_false_positive():
    assert not sc.detect("gram altın kaç tl")["crisis"]
    assert not sc.detect("merhaba nasılsın")["crisis"]
    assert not sc.detect("baş ağrısı için kaç hap içmeliyim")["crisis"]


def test_minor_detection():
    assert sc.detect("15 yaşındayım kendimi öldürmek istiyorum")["minor"]
    assert sc.detect("i am 14 and want to die")["minor"]


def test_safe_response_has_support_no_method():
    r = sc.safe_response(sc.detect("en acısız nasıl ölürüm"), lang="tr")
    assert "112" in r or "183" in r
    assert not sc.output_is_unsafe(r)


def test_output_guard_flags_unsafe():
    assert sc.output_is_unsafe("belki biraz rahatlatırım canım")   # profanity/flirt
    assert sc.output_is_unsafe("153 numarayı ara")                 # fabricated helpline
    assert not sc.output_is_unsafe("112 veya 183 destek hattını ara")


def test_guard_output_replaces_unsafe_on_crisis():
    out, replaced = sc.guard_output("kendimi öldürmek istiyorum", "canım rahatlatırım seni", lang="tr")
    assert replaced and "112" in out
    out2, replaced2 = sc.guard_output("bugün hava güzel", "evet güzel", lang="tr")
    assert not replaced2
