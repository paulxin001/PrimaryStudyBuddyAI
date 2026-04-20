"""文件编辑守卫 — 保护核心文件

对状态机、AI 抽象接口、不变量检查器等核心文件的修改，
要求用户确认。防止 Agent 在丢失上下文后误改关键逻辑。
"""
import json
import sys
from fnmatch import fnmatch


PROTECTED_FILES = [
    {
        "glob": "**/orchestrator/state_machine.py",
        "reason": "状态机是系统骨架（7 状态 + 转换规则）",
        "fix": "修改前请先确认：1) 阅读 TRANSITIONS 表 2) 同步更新 engine.py handler 3) 用户确认",
    },
    {
        "glob": "**/ai/base.py",
        "reason": "AIProvider 抽象接口是所有适配器的契约",
        "fix": "接口变更会影响所有适配器实现。请先确认变更范围。",
    },
    {
        "glob": "**/scripts/lint_invariants.py",
        "reason": "不变量检查器保护项目约束",
        "fix": "修改检查器可能放松或破坏机械化约束。请确认修改意图。",
    },
    {
        "glob": "**/config/settings.py",
        "reason": "全局配置影响所有模块行为",
        "fix": "修改默认值（特别是计时器参数）会影响孩子的使用体验。请确认。",
    },
    {
        "glob": "**/prompts/*.md",
        "reason": "Prompt 是品味编码，直接影响 AI 与孩子的交互质量",
        "fix": "Prompt 修改需用户确认。请展示修改前后的对比。",
    },
]


def check_edit(file_path: str) -> tuple[bool, str]:
    for rule in PROTECTED_FILES:
        if fnmatch(file_path, rule["glob"]):
            return False, f"🚫 {rule['reason']}\nFix: {rule['fix']}"
    return True, ""


def main():
    input_data = json.loads(sys.stdin.read())
    file_path = input_data.get("path", "")

    is_safe, reason = check_edit(file_path)
    if not is_safe:
        result = {"decision": "block", "reason": reason}
    else:
        result = {"decision": "approve"}

    print(json.dumps(result))


if __name__ == "__main__":
    main()
