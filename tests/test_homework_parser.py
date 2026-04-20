"""作业解析器单元测试"""
from __future__ import annotations

import pytest

from studybuddy.planner.homework_parser import parse_homework_text


# --- 基本解析 ---

def test_parse_subject_with_colon():
    tasks = parse_homework_text("数学：练习册第32页")
    assert len(tasks) == 1
    assert tasks[0].subject == "数学"
    assert "练习册" in tasks[0].description


def test_parse_subject_with_fullwidth_colon():
    tasks = parse_homework_text("语文：抄写生字")
    assert tasks[0].subject == "语文"


def test_parse_multiple_subjects():
    text = "数学：口算题卡\n语文：抄写生字两遍\n英语：听写单词"
    tasks = parse_homework_text(text)
    assert len(tasks) == 3
    subjects = [t.subject for t in tasks]
    assert "数学" in subjects
    assert "语文" in subjects
    assert "英语" in subjects


# --- 显式时间解析 ---

def test_explicit_duration_is_used():
    tasks = parse_homework_text("阅读30分钟")
    assert tasks[0].duration_minutes == 30


def test_explicit_duration_overrides_default():
    tasks = parse_homework_text("数学：口算题卡 10分钟")
    assert tasks[0].duration_minutes == 10


def test_default_duration_used_when_no_explicit():
    tasks = parse_homework_text("数学：练习册")
    assert tasks[0].duration_minutes == 20


# --- 编号前缀去除 ---

def test_numbered_list_prefix_removed():
    text = "1. 数学：口算\n2. 语文：生字"
    tasks = parse_homework_text(text)
    assert len(tasks) == 2
    assert tasks[0].subject == "数学"


def test_chinese_numbered_prefix_removed():
    text = "①数学：口算\n②语文：生字"
    tasks = parse_homework_text(text)
    assert len(tasks) == 2


# --- 科目猜测 ---

def test_guess_subject_from_keyword_math():
    tasks = parse_homework_text("口算练习20道题")
    assert tasks[0].subject in ("数学", "口算")


def test_guess_subject_from_keyword_chinese():
    tasks = parse_homework_text("抄写生字三遍")
    assert tasks[0].subject == "语文"


def test_guess_subject_from_keyword_english():
    tasks = parse_homework_text("听写单词15个")
    assert tasks[0].subject == "英语"


def test_unknown_subject_falls_back_to_other():
    tasks = parse_homework_text("做手工一件")
    assert tasks[0].subject in ("美术", "其他")


# --- 边界输入 ---

def test_empty_lines_are_skipped():
    text = "\n数学：口算\n\n语文：生字\n"
    tasks = parse_homework_text(text)
    assert len(tasks) == 2


def test_single_subject_no_description():
    tasks = parse_homework_text("数学")
    assert len(tasks) == 1
    assert tasks[0].subject == "数学"
