"""
HAWK Structured Logger — JSON formatında log çıktısı, merkezi hata sayacı.
"""
import logging
import json
import sys
import time
import threading
from typing import Any, Dict, Optional


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        doc: Dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            doc["exc"] = self.formatException(record.exc_info)
        extra = getattr(record, "extra", None)
        if extra:
            doc.update(extra)
        return json.dumps(doc, ensure_ascii=False)


def get_logger(name: str = "hawk") -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter())
    logger.addHandler(handler)
    logger.propagate = False
    return logger


# Singleton logger
log = get_logger("hawk")


# --- Metrics counter ---
class _Metrics:
    def __init__(self):
        self._lock = threading.Lock()
        self.requests_total = 0
        self.errors_total = 0
        self.chat_requests = 0
        self.started_at = time.time()

    def inc_request(self, path: str = ""):
        with self._lock:
            self.requests_total += 1
            if "chat" in path:
                self.chat_requests += 1

    def inc_error(self):
        with self._lock:
            self.errors_total += 1

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "uptime_seconds": round(time.time() - self.started_at),
                "requests_total": self.requests_total,
                "errors_total": self.errors_total,
                "chat_requests": self.chat_requests,
            }


metrics = _Metrics()
