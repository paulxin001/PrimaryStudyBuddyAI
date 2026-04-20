"""Orchestrator 任务编排引擎 — 系统的大脑

驱动状态机、管理 AI 会话生命周期、处理 function calling、
协调 Timer 与 AI 交互。孩子不需要任何操作就能推进流程。

RTC 模式下：
- 音视频由客户端 RTC SDK ↔ 火山引擎云端直接处理
- 服务端通过 OpenAPI 管控会话（StartVoiceChat/UpdateVoiceChat/StopVoiceChat）
- Function Call 通过 RTC 服务端回调 → Orchestrator 处理 → UpdateVoiceChat 回传
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import structlog

from ..ai.base import AIMessage, AIProvider, FunctionCall, FunctionResponse
from ..config.settings import config, SESSIONS_DIR, PROMPTS_DIR
from ..monitor.behavior_analyzer import BehaviorAnalyzer
from .state_machine import (
    Event,
    SessionContext,
    State,
    StudyPlan,
    StudySessionStateMachine,
)
from .timer import TimerManager

logger = structlog.get_logger()


class Orchestrator:
    """任务编排器

    整合状态机、AI Provider、计时器，形成完整的编排回路：
    1. 家长提交作业 → 生成计划
    2. 孩子连接 → 按计划自动推进
    3. AI 通过 function calling 报告状态 → Orchestrator 决策
    4. 全部完成 → 生成报告
    """

    def __init__(self, ai_provider: AIProvider):
        self.ai = ai_provider
        self.sm: StudySessionStateMachine | None = None
        self.timer = TimerManager()
        self._analyzer = BehaviorAnalyzer(
            window_seconds=config.timer.attention_check_interval_seconds * 4,
            threshold=0.5,
        )
        self._client_status_callback = None
        self._running = False
        self._room_id: str = ""

    async def create_session(self, plan: StudyPlan) -> SessionContext:
        ctx = SessionContext(plan=plan)
        self.sm = StudySessionStateMachine(ctx)
        self.sm.on_transition(self._on_state_change)
        self.timer.on_done(self._on_timer_done)

        self._persist_state()
        logger.info("session_created", session_id=plan.session_id, tasks=len(plan.tasks))
        return ctx

    def on_client_status(self, callback) -> None:
        self._client_status_callback = callback

    async def start(self, room_id: str, child_user_id: str) -> None:
        """孩子端连接后调用，启动整个监督流程

        RTC 模式下通过 OpenAPI 创建 AI 智能体加入 RTC 房间。
        """
        if not self.sm:
            raise RuntimeError("No session created")

        self._room_id = room_id
        self.sm.ctx.session_start_at = time.time()
        self._running = True

        system_prompt = self._build_system_prompt()
        await self.ai.connect(system_prompt)

        from ..ai.volcano_provider import VolcanoProvider
        if isinstance(self.ai, VolcanoProvider):
            task = self.sm.ctx.plan.current_task
            welcome = f"嗨，{self.sm.ctx.plan.child_name}！准备开始写作业啦！"
            await self.ai.start_voice_chat(
                room_id=room_id,
                task_id=self.sm.ctx.plan.session_id,
                child_user_id=child_user_id,
                welcome_message=welcome,
            )

        await self.sm.dispatch(Event.CHILD_CONNECTED)
        asyncio.create_task(self._ai_receive_loop())

    async def stop(self) -> None:
        self._running = False
        self.timer.cancel()
        if self.sm:
            await self.sm.shutdown()
        await self.ai.disconnect()

    # --- 状态转换处理 ---

    async def _on_state_change(
        self, prev: State, next_state: State, event: Event, ctx: SessionContext
    ) -> None:
        """每次状态转换时的核心编排逻辑"""
        handler = {
            State.TASK_BRIEFING: self._enter_task_briefing,
            State.STUDYING: self._enter_studying,
            State.NUDGE: self._enter_nudge,
            State.BREAK_TIME: self._enter_break_time,
            State.ALL_DONE: self._enter_all_done,
            State.REPORT_SENT: self._enter_report_sent,
        }.get(next_state)

        if handler:
            await handler(ctx)

        self._persist_state()
        await self._notify_client_status(ctx)

    async def _enter_task_briefing(self, ctx: SessionContext) -> None:
        task = ctx.plan.current_task
        if not task:
            return

        prompt = _load_prompt("task_briefing.md").format(
            subject=task.subject,
            description=task.description,
            duration=task.duration_minutes,
            task_number=ctx.plan.current_task_index + 1,
            total_tasks=len(ctx.plan.tasks),
        )
        await self.ai.send_text(prompt)

        self.sm.start_timer(
            config.timer.task_briefing_seconds,
            Event.BRIEFING_DONE,
        )

    async def _enter_studying(self, ctx: SessionContext) -> None:
        task = ctx.plan.current_task
        if not task:
            return

        ctx.study_start_times[ctx.plan.current_task_index] = time.time()
        self._analyzer = BehaviorAnalyzer(
            window_seconds=config.timer.attention_check_interval_seconds * 4,
            threshold=0.5,
        )

        prompt = _load_prompt("studying_monitor.md").format(
            subject=task.subject,
            description=task.description,
            duration=task.duration_minutes,
        )
        await self.ai.send_text(prompt)

        await self.timer.start(
            label=f"{task.subject} 学习时间",
            seconds=task.duration_minutes * 60,
        )

    async def _enter_nudge(self, ctx: SessionContext) -> None:
        ctx.nudge_count += 1
        ctx.last_nudge_at = time.time()
        self.timer.pause()

        prompt = _load_prompt("nudge_templates.md").format(
            nudge_count=ctx.nudge_count,
            subject=ctx.plan.current_task.subject if ctx.plan.current_task else "",
        )
        await self.ai.send_text(prompt)

        self.sm.start_timer(15, Event.ATTENTION_REGAINED)

    async def _enter_break_time(self, ctx: SessionContext) -> None:
        ctx.study_end_times[ctx.plan.current_task_index] = time.time()

        if ctx.plan.current_task:
            ctx.plan.current_task.completed = True

        if not ctx.plan.has_more_tasks:
            await self.sm.dispatch(Event.ALL_TASKS_FINISHED)
            return

        ctx.plan.advance_task()
        break_minutes = ctx.plan.tasks[ctx.plan.current_task_index - 1].break_after_minutes

        prompt = _load_prompt("break_time.md").format(
            break_minutes=break_minutes,
            next_subject=ctx.plan.current_task.subject if ctx.plan.current_task else "完成",
        )
        await self.ai.send_text(prompt)

        await self.timer.start(
            label="休息时间",
            seconds=break_minutes * 60,
        )

    async def _enter_all_done(self, ctx: SessionContext) -> None:
        ctx.session_end_at = time.time()

        summary = self._build_session_summary(ctx)
        prompt = _load_prompt("report_summary.md").format(
            child_name=ctx.plan.child_name,
            summary=json.dumps(summary, ensure_ascii=False, indent=2),
        )
        await self.ai.send_text(prompt)

        await asyncio.sleep(30)
        await self.sm.dispatch(Event.REPORT_GENERATED)

    async def _enter_report_sent(self, ctx: SessionContext) -> None:
        self._persist_report(ctx)
        await self.stop()

    # --- AI 消息处理 ---

    async def _ai_receive_loop(self) -> None:
        """持续接收 AI 消息（RTC 模式下主要接收 Function Call）"""
        try:
            async for msg in self.ai.receive():
                if not self._running:
                    break

                if msg.function_call:
                    await self._handle_function_call(msg.function_call)

                if msg.text:
                    logger.debug("ai_text", text=msg.text[:100])
        except Exception as e:
            logger.error("ai_receive_loop_error", error=str(e))

    async def _handle_function_call(self, fc: FunctionCall) -> None:
        """处理 AI 的 function calling — 这是 AI 与编排器的沟通通道"""
        logger.info("function_call", name=fc.name, args=fc.arguments)

        if fc.name == "report_status":
            await self._handle_status_report(fc)
        elif fc.name == "request_phase_change":
            await self._handle_phase_change_request(fc)

    async def _handle_status_report(self, fc: FunctionCall) -> None:
        status = fc.arguments.get("status", "")
        confidence = fc.arguments.get("confidence", 0)
        detail = fc.arguments.get("detail", "")

        self.sm.ctx.log_behavior(status, detail)
        self._analyzer.add_report(status, confidence, detail)

        cooldown = config.timer.nudge_cooldown_seconds
        if (self._analyzer.should_nudge()
                and time.time() - self.sm.ctx.last_nudge_at > cooldown):
            await self.sm.dispatch(Event.ATTENTION_LOST)

        await self.ai.send_function_response(FunctionResponse(
            name="report_status",
            result={"acknowledged": True, "action": "noted"},
        ))

    async def _handle_phase_change_request(self, fc: FunctionCall) -> None:
        reason = fc.arguments.get("reason", "")
        logger.info("phase_change_requested", reason=reason)

        if self.sm.state == State.STUDYING:
            await self.sm.dispatch(Event.TASK_COMPLETED_EARLY)

        await self.ai.send_function_response(FunctionResponse(
            name="request_phase_change",
            result={"acknowledged": True},
        ))

    async def _on_timer_done(self, timer_info) -> None:
        """计时器到期 — 背压门控触发"""
        if not self.sm:
            return

        if self.sm.state == State.STUDYING:
            await self.sm.dispatch(Event.STUDY_TIMER_UP)
        elif self.sm.state == State.BREAK_TIME:
            await self.sm.dispatch(Event.HAS_MORE_TASKS)

    # --- 辅助方法 ---

    def _build_system_prompt(self) -> str:
        persona = _load_prompt("system_persona.md")
        plan = self.sm.ctx.plan
        task_list = "\n".join(
            f"  {i+1}. {t.subject}：{t.description}（{t.duration_minutes}分钟）"
            for i, t in enumerate(plan.tasks)
        )
        return persona.format(
            child_name=plan.child_name,
            task_list=task_list,
            total_minutes=plan.total_minutes,
        )

    def _build_session_summary(self, ctx: SessionContext) -> dict:
        tasks_summary = []
        for i, task in enumerate(ctx.plan.tasks):
            start = ctx.study_start_times.get(i, 0)
            end = ctx.study_end_times.get(i, 0)
            actual_minutes = round((end - start) / 60, 1) if start and end else 0
            tasks_summary.append({
                "subject": task.subject,
                "planned_minutes": task.duration_minutes,
                "actual_minutes": actual_minutes,
                "completed": task.completed,
            })

        distractions = [e for e in ctx.behavior_log if e.event_type in
                        {"distracted", "bad_posture", "playing_with_pen", "looking_away", "child_left_seat"}]

        total_time = (ctx.session_end_at - ctx.session_start_at) / 60 if ctx.session_end_at else 0

        return {
            "session_id": ctx.plan.session_id,
            "child_name": ctx.plan.child_name,
            "total_minutes": round(total_time, 1),
            "tasks": tasks_summary,
            "nudge_count": ctx.nudge_count,
            "distraction_events": len(distractions),
            "focus_ratio": round(self._analyzer.get_focus_ratio(), 3),
            "behavior_highlights": [
                {"time": e.timestamp, "type": e.event_type, "detail": e.detail}
                for e in distractions[:10]
            ],
        }

    async def _notify_client_status(self, ctx: SessionContext) -> None:
        if self._client_status_callback:
            status = {
                "state": ctx.state.value,
                "task_index": ctx.plan.current_task_index,
                "task_total": len(ctx.plan.tasks),
                "current_task": ctx.plan.current_task.subject if ctx.plan.current_task else None,
                "timer": self.timer.current.to_dict() if self.timer.current else None,
                "nudge_count": ctx.nudge_count,
            }
            await self._client_status_callback(status)

    def _persist_state(self) -> None:
        if not self.sm:
            return
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        path = SESSIONS_DIR / f"{self.sm.ctx.plan.session_id}.json"
        data = {
            "session_id": self.sm.ctx.plan.session_id,
            "state": self.sm.ctx.state.value,
            "room_id": self._room_id,
            "plan": {
                "child_name": self.sm.ctx.plan.child_name,
                "current_task_index": self.sm.ctx.plan.current_task_index,
                "tasks": [
                    {"subject": t.subject, "description": t.description,
                     "duration_minutes": t.duration_minutes, "completed": t.completed}
                    for t in self.sm.ctx.plan.tasks
                ],
            },
            "nudge_count": self.sm.ctx.nudge_count,
            "behavior_log_count": len(self.sm.ctx.behavior_log),
        }
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _persist_report(self, ctx: SessionContext) -> None:
        from ..reporter.report_generator import generate_report
        generate_report(ctx)


def _load_prompt(filename: str) -> str:
    path = PROMPTS_DIR / filename
    if path.exists():
        return path.read_text(encoding="utf-8")
    logger.warning("prompt_not_found", filename=filename)
    return ""
