"""任务状态机 — 整个系统的核心编排逻辑

状态流转由 Orchestrator 驱动，不依赖孩子操作。
孩子只需要坐在那里写作业，其余全部自动化。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Awaitable

import structlog

logger = structlog.get_logger()


class State(str, Enum):
    PLAN_READY = "plan_ready"
    TASK_BRIEFING = "task_briefing"
    STUDYING = "studying"
    NUDGE = "nudge"
    BREAK_TIME = "break_time"
    ALL_DONE = "all_done"
    REPORT_SENT = "report_sent"


class Event(str, Enum):
    CHILD_CONNECTED = "child_connected"
    BRIEFING_DONE = "briefing_done"
    STUDY_TIMER_UP = "study_timer_up"
    TASK_COMPLETED_EARLY = "task_completed_early"
    ATTENTION_LOST = "attention_lost"
    ATTENTION_REGAINED = "attention_regained"
    BREAK_OVER = "break_over"
    ALL_TASKS_FINISHED = "all_tasks_finished"
    HAS_MORE_TASKS = "has_more_tasks"
    REPORT_GENERATED = "report_generated"


TRANSITIONS: dict[tuple[State, Event], State] = {
    (State.PLAN_READY, Event.CHILD_CONNECTED): State.TASK_BRIEFING,
    (State.TASK_BRIEFING, Event.BRIEFING_DONE): State.STUDYING,
    (State.STUDYING, Event.STUDY_TIMER_UP): State.BREAK_TIME,
    (State.STUDYING, Event.TASK_COMPLETED_EARLY): State.BREAK_TIME,
    (State.STUDYING, Event.ATTENTION_LOST): State.NUDGE,
    (State.NUDGE, Event.ATTENTION_REGAINED): State.STUDYING,
    (State.BREAK_TIME, Event.HAS_MORE_TASKS): State.TASK_BRIEFING,
    (State.BREAK_TIME, Event.ALL_TASKS_FINISHED): State.ALL_DONE,
    (State.ALL_DONE, Event.REPORT_GENERATED): State.REPORT_SENT,
}


@dataclass
class TaskItem:
    """单项作业任务"""
    subject: str
    description: str
    duration_minutes: int
    break_after_minutes: int = 5
    completed: bool = False


@dataclass
class StudyPlan:
    """学习计划"""
    session_id: str
    child_name: str
    tasks: list[TaskItem] = field(default_factory=list)
    current_task_index: int = 0
    created_at: float = field(default_factory=time.time)

    @property
    def current_task(self) -> TaskItem | None:
        if 0 <= self.current_task_index < len(self.tasks):
            return self.tasks[self.current_task_index]
        return None

    @property
    def has_more_tasks(self) -> bool:
        return self.current_task_index < len(self.tasks) - 1

    def advance_task(self) -> TaskItem | None:
        if self.current_task:
            self.current_task.completed = True
        self.current_task_index += 1
        return self.current_task

    @property
    def total_minutes(self) -> int:
        return sum(t.duration_minutes + t.break_after_minutes for t in self.tasks)

    @property
    def completed_count(self) -> int:
        return sum(1 for t in self.tasks if t.completed)


@dataclass
class BehaviorEvent:
    """行为事件记录"""
    timestamp: float
    event_type: str
    detail: str
    state: str


@dataclass
class SessionContext:
    """会话运行时上下文 — Disk Is State"""
    plan: StudyPlan
    state: State = State.PLAN_READY
    behavior_log: list[BehaviorEvent] = field(default_factory=list)
    state_entered_at: float = field(default_factory=time.time)
    nudge_count: int = 0
    last_nudge_at: float = 0
    study_start_times: dict[int, float] = field(default_factory=dict)
    study_end_times: dict[int, float] = field(default_factory=dict)
    session_start_at: float = 0
    session_end_at: float = 0

    def log_behavior(self, event_type: str, detail: str) -> None:
        self.behavior_log.append(BehaviorEvent(
            timestamp=time.time(),
            event_type=event_type,
            detail=detail,
            state=self.state.value,
        ))


TransitionCallback = Callable[[State, State, Event, SessionContext], Awaitable[None]]


class StudySessionStateMachine:
    """学习会话状态机

    约束越严，自主性越强 — AI 只能在当前状态的合法动作范围内行动。
    """

    def __init__(self, ctx: SessionContext):
        self.ctx = ctx
        self._callbacks: list[TransitionCallback] = []
        self._timer_task: asyncio.Task | None = None

    def on_transition(self, callback: TransitionCallback) -> None:
        self._callbacks.append(callback)

    @property
    def state(self) -> State:
        return self.ctx.state

    @property
    def allowed_events(self) -> list[Event]:
        return [ev for (s, ev), _ in TRANSITIONS.items() if s == self.ctx.state]

    async def dispatch(self, event: Event) -> State | None:
        """派发事件，触发状态转换。非法事件被静默忽略。"""
        key = (self.ctx.state, event)
        next_state = TRANSITIONS.get(key)
        if next_state is None:
            logger.warning(
                "invalid_transition",
                current=self.ctx.state.value,
                event=event.value,
            )
            return None

        prev = self.ctx.state
        self.ctx.state = next_state
        self.ctx.state_entered_at = time.time()
        self.ctx.log_behavior("state_transition", f"{prev.value} -> {next_state.value} via {event.value}")

        logger.info(
            "state_transition",
            prev=prev.value,
            next=next_state.value,
            event=event.value,
            task_index=self.ctx.plan.current_task_index,
        )

        for cb in self._callbacks:
            await cb(prev, next_state, event, self.ctx)

        return next_state

    def cancel_timer(self) -> None:
        if self._timer_task and not self._timer_task.done():
            self._timer_task.cancel()
            self._timer_task = None

    def start_timer(self, seconds: float, event: Event) -> None:
        """设定计时器，到时自动派发事件（背压门控）"""
        self.cancel_timer()

        async def _tick():
            try:
                await asyncio.sleep(seconds)
                await self.dispatch(event)
            except asyncio.CancelledError:
                pass

        self._timer_task = asyncio.create_task(_tick())
        logger.info("timer_started", seconds=seconds, event=event.value)

    async def shutdown(self) -> None:
        self.cancel_timer()
