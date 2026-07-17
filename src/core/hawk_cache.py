"""
HAWK Redis Cache — rate limiting persistence + response caching.

Yoksa in-memory fallback ile çalışır — sıfır kesinti.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Optional

_REDIS_URL = os.getenv("REDIS_URL", "redis://hawk-v2-redis:6379/0")
_redis = None
_AVAILABLE = False


async def _get_redis():
    global _redis, _AVAILABLE
    if _redis is not None:
        return _redis
    try:
        import redis.asyncio as aioredis
        _redis = await aioredis.from_url(_REDIS_URL, decode_responses=True, socket_connect_timeout=3)
        await _redis.ping()
        _AVAILABLE = True
        return _redis
    except Exception:
        _AVAILABLE = False
        return None


# ─── In-memory fallback ───────────────────────────────────────────────────────

_mem_cache: dict = {}   # key → (value, expires_at)
_mem_rl: dict = {}      # key → [timestamps]


def _mem_get(key: str) -> Optional[str]:
    entry = _mem_cache.get(key)
    if not entry:
        return None
    val, exp = entry
    if exp and time.monotonic() > exp:
        del _mem_cache[key]
        return None
    return val


def _mem_set(key: str, value: str, ttl: int = 0):
    exp = time.monotonic() + ttl if ttl else 0
    _mem_cache[key] = (value, exp)


def _mem_rl_check(key: str, limit: int, window_secs: int) -> bool:
    now = time.monotonic()
    bucket = _mem_rl.setdefault(key, [])
    cutoff = now - window_secs
    _mem_rl[key] = [t for t in bucket if t > cutoff]
    if len(_mem_rl[key]) >= limit:
        return False
    _mem_rl[key].append(now)
    return True


# ─── Public API ───────────────────────────────────────────────────────────────

async def cache_get(key: str) -> Optional[str]:
    r = await _get_redis()
    if r:
        try:
            return await r.get(f"hawk:{key}")
        except Exception:
            pass
    return _mem_get(key)


async def cache_set(key: str, value: Any, ttl: int = 300):
    v = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    r = await _get_redis()
    if r:
        try:
            await r.setex(f"hawk:{key}", ttl, v)
            return
        except Exception:
            pass
    _mem_set(key, v, ttl)


async def cache_delete(key: str):
    r = await _get_redis()
    if r:
        try:
            await r.delete(f"hawk:{key}")
            return
        except Exception:
            pass
    _mem_cache.pop(key, None)


async def rate_limit_check(key: str, limit: int = 60, window_secs: int = 60) -> bool:
    """True = izin ver, False = engelle. Redis sliding window veya in-memory."""
    r = await _get_redis()
    if r:
        try:
            now = time.time()
            pipe_key = f"rl:{key}"
            async with r.pipeline() as pipe:
                pipe.zremrangebyscore(pipe_key, "-inf", now - window_secs)
                pipe.zadd(pipe_key, {str(now): now})
                pipe.zcard(pipe_key)
                pipe.expire(pipe_key, window_secs + 1)
                results = await pipe.execute()
            count = results[2]
            return count <= limit
        except Exception:
            pass
    return _mem_rl_check(key, limit, window_secs)


async def cache_status() -> dict:
    r = await _get_redis()
    if r:
        try:
            info = await r.info("memory")
            return {
                "backend": "redis",
                "url": _REDIS_URL,
                "used_memory_human": info.get("used_memory_human"),
                "connected_clients": (await r.info("clients")).get("connected_clients"),
            }
        except Exception:
            pass
    return {"backend": "memory", "url": None, "keys": len(_mem_cache)}
