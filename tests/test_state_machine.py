"""状态机单元测试"""
from __future__ import annotations

import asyncio
import pytest

from studybuddy.orchestrator.state_machine import (
    Event,
    SessionContext,
    State,
    StudyPlan,
    StudySessionStateMachine,
    TaskItem,
)


def make_plan(n_tasks: int = 2) -> StudyPlan:
    return StudyPlan(
        session_id="test_session",
        child_name="小明",
        tasks=[
            TaskItem(subject=f"科目{i}", description="测试", duration_minutes=20)
            for i in range(n_tasks)
        ],
    )


def make_sm(n_tasks: int = 2) -> StudySessionStateMachine:
    ctx = SessionContext(plan=make_plan(n_tasks))
    return StudySessionStateMachine(ctx)


# --- 合法状态转换 ---

async def test_child_connected_transitions_to_task_briefing():
    sm = make_sm()
    assert sm.state == State.PLAN_READY
    result = await sm.dispatch(Event.CHILD_CONNECTED)
    assert result == State.TASK_BRIEFING
    assert sm.state == State.TASK_BRIEFING


async def test_briefing_done_transitions_to_studying():
    sm = make_sm()
    await sm.dispatch(Event.CHILD_CONNECTED)
    result = await sm.dispatch(Event.BRIEFING_DONE)
    assert result == State.STUDYING


async def test_attention_lost_transitions_to_nudge():
    sm = make_sm()
    await sm.dispatch(Event.CHILD_CONNECTED)
    await sm.dispatch(Event.BRIEFING_DONE)
    result = await sm.dispatch(Event.ATTENTION_LOST)
    assert result == State.NUDGE


async def test_attention_regained_returns_to_studying():
    sm = make_sm()
    await sm.dispatch(Event.CHILD_CONNECTED)
    await sm.dispatch(Event.BRIEFING_DONE)
    await sm.dispatch(Event.ATTENTION_LOST)
    result = await sm.dispatch(Event.ATTENTION_REGAINED)
    assert result == State.STUDYING


async def test_study_timer_up_transitions_to_break():
    sm = make_sm()
    await sm.dispatch(Event.CHILD_CONNECTED)
    await sm.dispatch(Event.BRIEFING_DONE)
    result = await sm.dispatch(Event.STUDY_TIMER_UP)
    assert result == State.BREAK_TIME


async def test_task_completed_early_transitions_to_break():
    sm = make_sm()
    await sm.dispatch(Event.CHILD_CONNECTED)
    await sm.dispatch(Event.BRIEFING_DONE)
    result = await sm.dispatch(Event.TASK_COMPLETED_EARLY)
    assert result == State.BREAK_TIME


async def test_break_has_more_tasks_transitions_to_briefing():
    sm = make_sm(n_tasks=2)
    await sm.dispatch(Event.CHILD_CONNECTED)
    await sm.dispatch(Event.BRIEFING_DONE)
    await sm.dispatch(Event.STUDY_TIMER_UP)
    result = await sm.dispatch(Event.HAS_MORE_TASKS)
    assert result == State.TASK_BRIEFING


async def test_all_tasks_finished_transitions_to_all_done():
    sm = make_sm()
    await sm.dispatch(Event.CHILD_CONNECTED)
    await sm.dispatch(Event.BRIEFING_DONE)
    await sm.dispatch(Event.STUDY_TIMER_UP)
    result = await sm.dispatch(Event.ALL_TASKS_FINISHED)
    assert result == State.ALL_DONE


async def test_report_generated_transitions_to_report_sent():
    sm = make_sm()
    await sm.dispatch(Event.CHILD_CONNECTED)
    await sm.dispatch(Event.BRIEFING_DONE)
    await sm.dispatch(Event.STUDY_TIMER_UP)
    await sm.dispatch(Event.ALL_TASKS_FINISHED)
    result = await sm.dispatch(Event.REPORT_GENERATED)
    assert result == State.REPORT_SENT


# --- 非法事件静默忽略 ---

async def test_invalid_event_returns_none():
    sm = make_sm()
    result = await sm.dispatch(Event.BRIEFING_DONE)
    assert result is None
    assert sm.state == State.PLAN_READY


async def test_invalid_event_does_not_change_state():
    sm = make_sm()
    await sm.dispatch(Event.CHILD_CONNECTED)
    assert sm.state == State.TASK_BRIEFING
    await sm.dispatch(Event.ATTENTION_LOST)
    assert sm.state == State.TASK_BRIEFING


# --- 回调触发 ---

async def test_transition_callback_is_called():
    sm = make_sm()
    called = []

    async def cb(prev, next_state, event, ctx):
        called.append((prev, next_state, event))

    sm.on_transition(cb)
    await sm.dispatch(Event.CHILD_CONNECTED)

    assert len(called) == 1
    assert called[0] == (State.PLAN_READY, State.TASK_BRIEFING, Event.CHILD_CONNECTED)


# --- 状态进入时间记录 ---

async def test_state_entered_at_is_updated():
    sm = make_sm()
    t0 = sm.ctx.state_entered_at
    await asyncio.sleep(0.01)
    await sm.dispatch(Event.CHILD_CONNECTED)
    assert sm.ctx.state_entered_at > t0


# --- allowed_events ---

def test_allowed_events_in_plan_ready():
    sm = make_sm()
    assert Event.CHILD_CONNECTED in sm.allowed_events


def test_allowed_events_in_studying():
    sm = make_sm()

    async def run():
        await sm.dispatch(Event.CHILD_CONNECTED)
        await sm.dispatch(Event.BRIEFING_DONE)

    asyncio.get_event_loop().run_until_complete(run())
    events = sm.allowed_events
    assert Event.STUDY_TIMER_UP in events
    assert Event.ATTENTION_LOST in events
    assert Event.TASK_COMPLETED_EARLY in events
