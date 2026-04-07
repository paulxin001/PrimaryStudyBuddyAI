"""作业解析器 — 将家长输入的文本解析为结构化任务列表"""

from __future__ import annotations

import re

from ..orchestrator.state_machine import TaskItem

DEFAULT_DURATIONS = {
    "语文": 25,
    "数学": 20,
    "英语": 20,
    "科学": 15,
    "默写": 10,
    "阅读": 20,
    "练字": 15,
    "口算": 10,
}

DEFAULT_BREAK = 5


def parse_homework_text(text: str) -> list[TaskItem]:
    """解析自由文本格式的作业内容

    支持格式:
    - 数学：练习册第32页
    - 语文 抄写生字 20分钟
    - 1. 英语听写单词
    - 阅读30分钟
    """
    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    tasks = []

    for line in lines:
        line = re.sub(r"^[\d]+[.、)）]\s*", "", line)

        duration_match = re.search(r"(\d+)\s*分钟", line)
        explicit_duration = int(duration_match.group(1)) if duration_match else None

        subject = None
        description = line

        for subj in DEFAULT_DURATIONS:
            if subj in line:
                subject = subj
                desc_part = line.replace(subj, "").strip()
                desc_part = re.sub(r"^[：:—\-\s]+", "", desc_part)
                if duration_match:
                    desc_part = re.sub(r"\d+\s*分钟", "", desc_part).strip()
                if desc_part:
                    description = desc_part
                else:
                    description = f"{subj}作业"
                break

        if not subject:
            subject = _guess_subject(line)
            if duration_match:
                description = re.sub(r"\d+\s*分钟", "", line).strip()

        duration = explicit_duration or DEFAULT_DURATIONS.get(subject, 20)

        tasks.append(TaskItem(
            subject=subject,
            description=description,
            duration_minutes=duration,
            break_after_minutes=DEFAULT_BREAK,
        ))

    return tasks


def _guess_subject(text: str) -> str:
    keywords = {
        "写字": "语文", "生字": "语文", "课文": "语文", "作文": "语文",
        "组词": "语文", "造句": "语文", "拼音": "语文", "笔顺": "语文",
        "加减": "数学", "乘除": "数学", "计算": "数学", "应用题": "数学",
        "口算": "数学", "练习册": "数学",
        "单词": "英语", "听写": "英语", "朗读": "英语",
        "画画": "美术", "手工": "美术",
        "读书": "阅读", "课外": "阅读",
    }
    for kw, subj in keywords.items():
        if kw in text:
            return subj
    return "其他"
