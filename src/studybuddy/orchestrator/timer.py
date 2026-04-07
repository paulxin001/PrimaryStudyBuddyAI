"""计时器管理 — 背压门控

计时器是状态机的"背压"机制：到时间必须触发状态转换，
不依赖孩子操作，保证流程自动推进。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

import structlog

logger = structlog.get_logger()


@dataclass
class TimerInfo:
    label: str
    total_seconds: float
    started_at: float = field(default_factory=time.time)
    paused_elapsed: float = 0
    is_paused: bool = False

    @property
    def elapsed(self) -> float:
        if self.is_paused:
            return self.paused_elapsed
        return self.paused_elapsed + (time.time() - self.started_at)

    @property
    def remaining(self) -> float:
        return max(0, self.total_seconds - self.elapsed)

    @property
    def progress(self) -> float:
        if self.total_seconds <= 0:
            return 1.0
        return min(1.0, self.elapsed / self.total_seconds)

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "total_seconds": self.total_seconds,
            "elapsed": round(self.elapsed, 1),
            "remaining": round(self.remaining, 1),
            "progress": round(self.progress, 3),
            "is_paused": self.is_paused,
        }


class TimerManager:
    """管理学习/休息计时器"""

    def __init__(self):
        self._current: TimerInfo | None = None
        self._task: asyncio.Task | None = None
        self._tick_callbacks: list = []
        self._done_callbacks: list = []

    @property
    def current(self) -> TimerInfo | None:
        return self._current

    def on_tick(self, callback) -> None:
        """每秒回调，用于推送进度到客户端"""
        self._tick_callbacks.append(callback)

    def on_done(self, callback) -> None:
        """计时结束回调"""
        self._done_callbacks.append(callback)

    async def start(self, label: str, seconds: float) -> TimerInfo:
        self.cancel()
        self._current = TimerInfo(label=label, total_seconds=seconds)
        self._task = asyncio.create_task(self._run())
        logger.info("timer_started", label=label, seconds=seconds)
        return self._current

    async def _run(self) -> None:
        try:
            while self._current and self._current.remaining > 0:
                await asyncio.sleep(1)
                if self._current and not self._current.is_paused:
                    for cb in self._tick_callbacks:
                        await cb(self._current)

            if self._current:
                logger.info("timer_done", label=self._current.label)
                for cb in self._done_callbacks:
                    await cb(self._current)
        except asyncio.CancelledError:
            pass

    def pause(self) -> None:
        if self._current and not self._current.is_paused:
            self._current.paused_elapsed = self._current.elapsed
            self._current.is_paused = True
            logger.info("timer_paused", label=self._current.label)

    def resume(self) -> None:
        if self._current and self._current.is_paused:
            self._current.started_at = time.time()
            self._current.is_paused = False
            logger.info("timer_resumed", label=self._current.label)

    def add_time(self, seconds: float) -> None:
        """弹性调整：增减时间"""
        if self._current:
            self._current.total_seconds += seconds
            logger.info("timer_adjusted", label=self._current.label, added=seconds)

    def cancel(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
        self._current = None
