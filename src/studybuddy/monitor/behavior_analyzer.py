"""行为分析模块 — 基于 AI 视觉输出判定坐姿/注意力

这个模块不直接做视觉分析（交给 Gemini），而是：
1. 聚合 AI 报告的状态事件
2. 做滑动窗口分析（避免单次误判导致误提醒）
3. 向 Orchestrator 提供聚合后的判定
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum


class AttentionLevel(str, Enum):
    FOCUSED = "focused"
    SLIGHTLY_DISTRACTED = "slightly_distracted"
    DISTRACTED = "distracted"
    ABSENT = "absent"


@dataclass
class StatusReport:
    timestamp: float
    status: str
    confidence: float
    detail: str = ""


class BehaviorAnalyzer:
    """行为分析器 — 滑动窗口聚合 + 防误判

    不是靠单次 AI 报告就触发提醒，而是在一个时间窗口内
    累积足够的"分心"报告才判定为真正的分心。
    """

    DISTRACTED_STATUSES = frozenset({
        "distracted", "bad_posture", "playing_with_pen",
        "looking_away", "child_left_seat",
    })

    def __init__(self, window_seconds: float = 60, threshold: float = 0.5):
        self._window = window_seconds
        self._threshold = threshold
        self._reports: deque[StatusReport] = deque()
        self._total_focused_time: float = 0
        self._total_distracted_time: float = 0
        self._last_report_time: float = 0

    def add_report(self, status: str, confidence: float, detail: str = "") -> None:
        now = time.time()
        self._reports.append(StatusReport(
            timestamp=now,
            status=status,
            confidence=confidence,
            detail=detail,
        ))
        self._prune_old_reports()

        if self._last_report_time > 0:
            delta = now - self._last_report_time
            if status == "focused":
                self._total_focused_time += delta
            elif status in self.DISTRACTED_STATUSES:
                self._total_distracted_time += delta
        self._last_report_time = now

    def get_attention_level(self) -> AttentionLevel:
        """基于滑动窗口内的报告，给出聚合判定"""
        self._prune_old_reports()

        if not self._reports:
            return AttentionLevel.FOCUSED

        recent = list(self._reports)
        distracted_score = sum(
            r.confidence for r in recent
            if r.status in self.DISTRACTED_STATUSES
        )
        total_score = sum(r.confidence for r in recent)

        if total_score == 0:
            return AttentionLevel.FOCUSED

        ratio = distracted_score / total_score

        if ratio < 0.2:
            return AttentionLevel.FOCUSED
        elif ratio < self._threshold:
            return AttentionLevel.SLIGHTLY_DISTRACTED
        elif ratio < 0.8:
            return AttentionLevel.DISTRACTED
        else:
            return AttentionLevel.ABSENT

    def should_nudge(self) -> bool:
        level = self.get_attention_level()
        return level in (AttentionLevel.DISTRACTED, AttentionLevel.ABSENT)

    def get_focus_ratio(self) -> float:
        """获取整体专注比例 0-1"""
        total = self._total_focused_time + self._total_distracted_time
        if total == 0:
            return 1.0
        return self._total_focused_time / total

    def get_stats(self) -> dict:
        return {
            "attention_level": self.get_attention_level().value,
            "focus_ratio": round(self.get_focus_ratio(), 3),
            "total_focused_minutes": round(self._total_focused_time / 60, 1),
            "total_distracted_minutes": round(self._total_distracted_time / 60, 1),
            "reports_in_window": len(self._reports),
        }

    def _prune_old_reports(self) -> None:
        cutoff = time.time() - self._window
        while self._reports and self._reports[0].timestamp < cutoff:
            self._reports.popleft()
