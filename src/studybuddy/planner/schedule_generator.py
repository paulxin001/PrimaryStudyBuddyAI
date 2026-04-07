"""时间计划生成器 — 根据任务列表生成合理的学习计划"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from ..orchestrator.state_machine import StudyPlan, TaskItem


def generate_plan(
    child_name: str,
    tasks: list[TaskItem],
    start_time: datetime | None = None,
) -> StudyPlan:
    """生成学习计划

    策略：
    - 高难度科目（数学）放前面，精力最充沛时完成
    - 连续学习不超过25分钟，强制插入休息
    - 最后一个任务不需要休息
    """
    if not tasks:
        raise ValueError("至少需要一个任务")

    sorted_tasks = _smart_sort(tasks)

    if len(sorted_tasks) > 0:
        sorted_tasks[-1].break_after_minutes = 0

    for task in sorted_tasks:
        if task.duration_minutes > 30:
            task.duration_minutes = 30

    return StudyPlan(
        session_id=_generate_session_id(),
        child_name=child_name,
        tasks=sorted_tasks,
    )


def _smart_sort(tasks: list[TaskItem]) -> list[TaskItem]:
    """智能排序：难的放前面，轻松的放后面"""
    priority = {
        "数学": 1,
        "英语": 2,
        "语文": 3,
        "科学": 4,
        "其他": 5,
        "阅读": 6,
        "美术": 7,
    }
    return sorted(tasks, key=lambda t: priority.get(t.subject, 5))


def format_plan_display(plan: StudyPlan) -> str:
    """生成人类可读的计划展示"""
    lines = [f"📋 {plan.child_name} 的学习计划", ""]
    current = datetime.now()

    for i, task in enumerate(plan.tasks):
        end = current + timedelta(minutes=task.duration_minutes)
        lines.append(
            f"  {i+1}. [{current.strftime('%H:%M')}-{end.strftime('%H:%M')}] "
            f"{task.subject}：{task.description}（{task.duration_minutes}分钟）"
        )
        current = end
        if task.break_after_minutes > 0:
            rest_end = current + timedelta(minutes=task.break_after_minutes)
            lines.append(f"     ☕ 休息 {task.break_after_minutes} 分钟")
            current = rest_end

    lines.append(f"\n  预计总时长：{plan.total_minutes} 分钟")
    lines.append(f"  预计完成时间：{current.strftime('%H:%M')}")
    return "\n".join(lines)


def _generate_session_id() -> str:
    now = datetime.now().strftime("%Y%m%d_%H%M%S")
    short_id = uuid.uuid4().hex[:6]
    return f"session_{now}_{short_id}"
