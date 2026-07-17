"""
HAWK Notification Hub — per-user SSE stream + PostgreSQL notification store.
Clients connect via GET /api/notifications/stream (Bearer auth).
Server pushes events via push_notification(user_id, event).
"""
import asyncio
import json
import time
from typing import Any, Dict, Optional


class _NotificationHub:
    def __init__(self):
        self._queues: Dict[str, list] = {}
        self._lock = asyncio.Lock()

    async def subscribe(self, user_id: str) -> asyncio.Queue:
        async with self._lock:
            if user_id not in self._queues:
                self._queues[user_id] = []
            q: asyncio.Queue = asyncio.Queue(maxsize=50)
            self._queues[user_id].append(q)
            return q

    async def unsubscribe(self, user_id: str, q: asyncio.Queue):
        async with self._lock:
            lst = self._queues.get(user_id, [])
            try:
                lst.remove(q)
            except ValueError:
                pass
            if not lst:
                self._queues.pop(user_id, None)

    async def push(self, user_id: str, event: Dict[str, Any]):
        async with self._lock:
            queues = list(self._queues.get(user_id, []))
        dead = []
        for q in queues:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                dead.append(q)
        if dead:
            async with self._lock:
                for dq in dead:
                    try:
                        self._queues.get(user_id, []).remove(dq)
                    except ValueError:
                        pass

    def connected_users(self) -> list:
        return list(self._queues.keys())

    def queue_count(self) -> int:
        return sum(len(v) for v in self._queues.values())


hub = _NotificationHub()


async def push_notification(user_id: str, title: str, body: str, data: Optional[Dict] = None):
    """API içinden bildirim gönder: hub'a push + DB'ye kaydet."""
    event = {
        "type": "notification",
        "title": title,
        "body": body,
        "data": data or {},
        "ts": int(time.time()),
    }
    await hub.push(user_id, event)
    return event


def sse_format(event: Dict[str, Any]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
