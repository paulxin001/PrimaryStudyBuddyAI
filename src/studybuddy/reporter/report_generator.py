"""报告生成器 — 生成家长可查看的学习报告"""

from __future__ import annotations

import json
import time
from pathlib import Path

import structlog

from ..config.settings import REPORTS_DIR
from ..orchestrator.state_machine import SessionContext

logger = structlog.get_logger()


def generate_report(ctx: SessionContext) -> dict:
    """根据会话上下文生成结构化学习报告"""

    tasks_detail = []
    for i, task in enumerate(ctx.plan.tasks):
        start = ctx.study_start_times.get(i, 0)
        end = ctx.study_end_times.get(i, 0)
        actual = round((end - start) / 60, 1) if start and end else 0

        task_distractions = [
            e for e in ctx.behavior_log
            if e.state == "studying"
            and start <= e.timestamp <= end
            and e.event_type in {"distracted", "bad_posture", "playing_with_pen", "looking_away"}
        ] if start and end else []

        tasks_detail.append({
            "subject": task.subject,
            "description": task.description,
            "planned_minutes": task.duration_minutes,
            "actual_minutes": actual,
            "completed": task.completed,
            "distraction_count": len(task_distractions),
            "efficiency": _calc_efficiency(task.duration_minutes, actual, len(task_distractions)),
        })

    total_planned = sum(t.duration_minutes for t in ctx.plan.tasks)
    total_actual = sum(d["actual_minutes"] for d in tasks_detail)
    total_session = round((ctx.session_end_at - ctx.session_start_at) / 60, 1) if ctx.session_end_at else 0

    report = {
        "session_id": ctx.plan.session_id,
        "child_name": ctx.plan.child_name,
        "date": time.strftime("%Y-%m-%d"),
        "overview": {
            "total_tasks": len(ctx.plan.tasks),
            "completed_tasks": ctx.plan.completed_count,
            "planned_minutes": total_planned,
            "actual_study_minutes": total_actual,
            "total_session_minutes": total_session,
            "nudge_count": ctx.nudge_count,
            "overall_rating": _calc_overall_rating(ctx),
        },
        "tasks": tasks_detail,
        "behavior_summary": _summarize_behaviors(ctx),
        "parent_tips": _generate_tips(ctx),
    }

    _save_report(report)
    return report


def _calc_efficiency(planned: int, actual: float, distractions: int) -> str:
    if actual == 0:
        return "未开始"
    ratio = planned / actual if actual > 0 else 0
    if ratio >= 0.9 and distractions <= 2:
        return "优秀"
    elif ratio >= 0.7 and distractions <= 5:
        return "良好"
    elif ratio >= 0.5:
        return "一般"
    else:
        return "需加强"


def _calc_overall_rating(ctx: SessionContext) -> str:
    completed_ratio = ctx.plan.completed_count / max(len(ctx.plan.tasks), 1)
    if completed_ratio >= 1.0 and ctx.nudge_count <= 3:
        return "非常棒"
    elif completed_ratio >= 1.0:
        return "很好，全部完成"
    elif completed_ratio >= 0.5:
        return "还不错，继续努力"
    else:
        return "需要加油"


def _summarize_behaviors(ctx: SessionContext) -> dict:
    type_counts: dict[str, int] = {}
    for event in ctx.behavior_log:
        if event.event_type != "state_transition":
            type_counts[event.event_type] = type_counts.get(event.event_type, 0) + 1

    posture_issues = sum(
        1 for e in ctx.behavior_log if e.event_type == "bad_posture"
    )

    return {
        "event_counts": type_counts,
        "posture_issues": posture_issues,
        "total_nudges": ctx.nudge_count,
    }


def _generate_tips(ctx: SessionContext) -> list[str]:
    tips = []

    if ctx.nudge_count > 5:
        tips.append("孩子注意力容易分散，可以尝试缩短单次学习时间（从25分钟减到15分钟），再逐步增加。")

    posture_issues = sum(1 for e in ctx.behavior_log if e.event_type == "bad_posture")
    if posture_issues > 3:
        tips.append("坐姿需要注意，建议检查桌椅高度是否合适，或者购买坐姿矫正器。")

    if ctx.plan.completed_count == len(ctx.plan.tasks) and ctx.nudge_count <= 2:
        tips.append("今天表现非常好！可以给孩子一些额外的奖励来强化这种好习惯。")

    if not tips:
        tips.append("整体表现不错，继续保持！")

    return tips


def _save_report(report: dict) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{report['session_id']}_report.json"
    path = REPORTS_DIR / filename
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("report_saved", path=str(path))
    return path


def format_report_for_parent(report: dict) -> str:
    """格式化为家长友好的文本展示"""
    ov = report["overview"]
    lines = [
        f"📊 {report['child_name']} 的学习报告 — {report['date']}",
        f"",
        f"⏱ 总时长：{ov['total_session_minutes']} 分钟",
        f"✅ 完成：{ov['completed_tasks']}/{ov['total_tasks']} 项任务",
        f"🔔 提醒次数：{ov['nudge_count']} 次",
        f"⭐ 总评：{ov['overall_rating']}",
        f"",
        f"--- 各科详情 ---",
    ]

    for task in report["tasks"]:
        status = "✅" if task["completed"] else "⬜"
        lines.append(
            f"{status} {task['subject']}：{task['actual_minutes']}分钟"
            f"（计划{task['planned_minutes']}分钟）— {task['efficiency']}"
        )

    if report.get("parent_tips"):
        lines.append(f"\n--- 建议 ---")
        for tip in report["parent_tips"]:
            lines.append(f"💡 {tip}")

    return "\n".join(lines)
