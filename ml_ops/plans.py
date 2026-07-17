"""
HAWK Base / Code sürüm planları + model bağımsızlık aşamaları (Bölüm 12-14).
Yapılandırılmış veri — roadmap tek doğruluk kaynağı; UI/rapor buradan okur.
"""
from __future__ import annotations

BASE_PLAN = {
    "hawk-base-v0.1": {
        "goals": ["doğal TR/EN sohbet", "hafıza bağlamı", "web sentezi",
                  "temel reasoning", "structured JSON", "temel tool calling",
                  "temel Workspace görevleri", "Independent mode temel hizmet"],
    },
    "hawk-base-v0.2": {
        "goals": ["daha iyi Türkçe", "daha iyi tool calling", "daha düşük hallucination",
                  "daha iyi proje hafızası", "daha iyi maliyet/latency"],
    },
    "hawk-base-v1.0": {
        "goals": ["production seviyesinde local-first", "güvenli multi-agent uyumu",
                  "yüksek tool doğruluğu", "benchmark kapıları geçmiş",
                  "shadow/canary tamamlanmış", "external expert yalnız zor görevlerde"],
    },
}

CODE_PLAN = {
    "hawk-code-v0.1": {
        "goals": ["repo haritalama", "bug bulma", "patch üretme", "unit test yazma",
                  "log analizi", "diff üretme"],
    },
    "hawk-code-v0.2": {
        "goals": ["build hatası düzeltme", "birden fazla dosya", "retry/replan",
                  "reviewer uyumu"],
    },
    "hawk-code-v1.0": {
        "goals": ["gerçek Workspace görev döngüsü", "test/build/review kanıtı",
                  "güvenli terminal/tool kullanımı", "push/deploy öncesi durma"],
    },
}

# model bağımsızlık aşamaları
INDEPENDENCE_PHASES = {
    1: {"desc": "dış model production, local model shadow"},
    2: {"desc": "basit görevler local, zor görevler external"},
    3: {"desc": "local-first hybrid, external yalnız düşük güven + zor görev"},
    4: {"desc": "Independent mode temel hizmet tamamen local, external isteğe bağlı"},
}

# API key/kredi bittiğinde ÇALIŞMAYA DEVAM etmesi gereken yetenekler (Bölüm 14)
INDEPENDENT_MUST_WORK = (
    "auth", "memory", "forget", "web", "file", "tool_system",
    "multi_agent", "basic_chat", "basic_workspace",
)


def plan_for(role: str) -> dict:
    return {"hawk_base": BASE_PLAN, "hawk_code": CODE_PLAN}.get(role, {})
