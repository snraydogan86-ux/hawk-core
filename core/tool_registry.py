"""
HAWK Tool Registry — agent orchestration'da kullanılacak araçların merkezi kaydı.
Her tool: name, description, fn (async callable), schema (input params).
"""
import asyncio
import os
from typing import Any, Callable, Dict, List, Optional


_REGISTRY: Dict[str, Dict] = {}


def register_tool(name: str, description: str, fn: Callable, schema: Optional[Dict] = None):
    _REGISTRY[name] = {
        "name": name,
        "description": description,
        "fn": fn,
        "schema": schema or {},
    }


def get_tool(name: str) -> Optional[Dict]:
    return _REGISTRY.get(name)


def list_tools() -> List[Dict]:
    return [
        {"name": t["name"], "description": t["description"], "schema": t["schema"]}
        for t in _REGISTRY.values()
    ]


async def call_tool(name: str, **kwargs) -> Any:
    """Araç çağırır. Sync fonksiyonları thread pool'da çalıştırır."""
    tool = _REGISTRY.get(name)
    if not tool:
        raise ValueError(f"Tool bulunamadı: {name}")
    fn = tool["fn"]
    if asyncio.iscoroutinefunction(fn):
        return await fn(**kwargs)
    return await asyncio.to_thread(fn, **kwargs)


async def call_tools_parallel(calls: List[Dict]) -> List[Any]:
    """
    Birden fazla tool çağrısını paralel çalıştırır.
    calls: [{"name": "tool_name", "kwargs": {...}}, ...]
    """
    tasks = [call_tool(c["name"], **c.get("kwargs", {})) for c in calls]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    return [None if isinstance(r, Exception) else r for r in results]


# --- Built-in tool registrations ---
def _register_builtins():
    from core.memory_store import recall_fact, remember_fact, memory_snapshot
    from core.agent_router import brave_search, fetch_finance_live_answer
    from core.pg_memory import pg_search_facts

    register_tool(
        name="recall_fact",
        description="Kullanıcıya ait kayıtlı bir bilgiyi getirir",
        fn=lambda key, user_id="default_user": recall_fact(key, user_id=user_id),
        schema={"key": "str", "user_id": "str (optional)"},
    )

    register_tool(
        name="remember_fact",
        description="Kullanıcıya ait bir bilgiyi kaydeder",
        fn=lambda key, value, user_id="default_user": remember_fact(key, value, user_id=user_id),
        schema={"key": "str", "value": "str", "user_id": "str (optional)"},
    )

    register_tool(
        name="memory_snapshot",
        description="Kullanıcının tüm kayıtlı bilgilerini ve geçmiş konuşmaları getirir",
        fn=lambda user_id="default_user": memory_snapshot(user_id=user_id),
        schema={"user_id": "str (optional)"},
    )

    register_tool(
        name="search_facts",
        description="Kullanıcının hafızasında anahtar kelimeyle arama yapar",
        fn=lambda query, user_id="default_user": pg_search_facts(user_id, query),
        schema={"query": "str", "user_id": "str (optional)"},
    )

    register_tool(
        name="brave_search",
        description="web search API ile web araması yapar",
        fn=brave_search,
        schema={"query": "str"},
    )

    register_tool(
        name="fetch_finance",
        description="Döviz, altın veya kripto para canlı verisi çeker",
        fn=fetch_finance_live_answer,
        schema={"message": "str"},
    )


try:
    _register_builtins()
except Exception:
    pass
