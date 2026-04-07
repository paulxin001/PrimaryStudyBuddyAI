"""会话日志记录器 — 结构化日志持久化"""

from __future__ import annotations

import json
import time
from pathlib import Path

import structlog

from ..config.settings import LOGS_DIR

logger = structlog.get_logger()


class SessionLogger:
    """记录完整的会话日志到磁盘

    遵循 Harness 原则：Disk Is State, Git Is Memory
    """

    def __init__(self, session_id: str):
        self.session_id = session_id
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        self._path = LOGS_DIR / f"{session_id}.jsonl"
        self._file = open(self._path, "a", encoding="utf-8")

    def log(self, event_type: str, **data) -> None:
        entry = {
            "ts": time.time(),
            "t": time.strftime("%H:%M:%S"),
            "type": event_type,
            **data,
        }
        self._file.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self._file.flush()

    def close(self) -> None:
        self._file.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
