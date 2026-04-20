"""计划生成器单元测试"""
from __future__ import annotations

import pytest

from studybuddy.orchestrator.state_machine import TaskItem
from studybuddy.planner.schedule_generator import generate_plan, format_plan_display


def make_tasks(*subjects: str) -> list[TaskItem]:
    return [
        TaskItem(subject=s, description=f"{s}作业", duration_minutes=20)
        for s in subjects
    ]


# --- 基本生成 ---

def test_generate_plan_returns_study_plan():
    from studybuddy.orchestrator.state_machine import StudyPlan
    plan = generate_plan("小明", make_tasks("数学", "语文"))
    assert isinstance(plan, StudyPlan)
    assert plan.child_name == "小明"


def test_generate_plan_preserves_task_count():
    plan = generate_plan("小明", make_tasks("数学", "语文", "英语"))
    assert len(plan.tasks) == 3


def test_generate_plan_requires_at_least_one_task():
    with pytest.raises(ValueError):
        generate_plan("小明", [])


# --- 排序策略 ---

def test_math_sorted_before_chinese():
    plan = generate_plan("小明", make_tasks("语文", "数学"))
    subjects = [t.subject for t in plan.tasks]
    assert subjects.index("数学") < subjects.index("语文")


def test_math_sorted_before_reading():
    plan = generate_plan("小明", make_tasks("阅读", "数学"))
    subjects = [t.subject for t in plan.tasks]
    assert subjects.index("数学") < subjects.index("阅读")


def test_english_sorted_before_chinese():
    plan = generate_plan("小明", make_tasks("语文", "英语"))
    subjects = [t.subject for t in plan.tasks]
    assert subjects.index("英语") < subjects.index("语文")


# --- 时长限制 ---

def test_duration_capped_at_30_minutes():
    tasks = [TaskItem(subject="数学", description="超长作业", duration_minutes=60)]
    plan = generate_plan("小明", tasks)
    assert plan.tasks[0].duration_minutes == 30


def test_duration_within_30_is_unchanged():
    tasks = [TaskItem(subject="数学", description="作业", duration_minutes=25)]
    plan = generate_plan("小明", tasks)
    assert plan.tasks[0].duration_minutes == 25


# --- 最后任务无休息 ---

def test_last_task_has_no_break():
    plan = generate_plan("小明", make_tasks("数学", "语文"))
    assert plan.tasks[-1].break_after_minutes == 0


def test_non_last_task_has_break():
    plan = generate_plan("小明", make_tasks("数学", "语文"))
    assert plan.tasks[0].break_after_minutes > 0


# --- total_minutes 计算 ---

def test_total_minutes_includes_study_and_break():
    tasks = [
        TaskItem(subject="数学", description="作业", duration_minutes=20, break_after_minutes=5),
        TaskItem(subject="语文", description="作业", duration_minutes=20, break_after_minutes=0),
    ]
    plan = generate_plan("小明", tasks)
    total = sum(t.duration_minutes + t.break_after_minutes for t in plan.tasks)
    assert plan.total_minutes == total


# --- session_id 唯一性 ---

def test_session_id_is_unique():
    plan1 = generate_plan("小明", make_tasks("数学"))
    plan2 = generate_plan("小明", make_tasks("数学"))
    assert plan1.session_id != plan2.session_id


# --- format_plan_display ---

def test_format_plan_display_contains_child_name():
    plan = generate_plan("小红", make_tasks("数学", "语文"))
    display = format_plan_display(plan)
    assert "小红" in display


def test_format_plan_display_contains_subjects():
    plan = generate_plan("小明", make_tasks("数学", "语文"))
    display = format_plan_display(plan)
    assert "数学" in display
    assert "语文" in display
