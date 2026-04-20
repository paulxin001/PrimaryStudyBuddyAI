"""Shell 命令守卫 — 拦截危险操作

拦截对 StudyBuddyAI 核心数据和配置的破坏性 shell 命令。
"""
import json
import re
import sys


BLOCKED_PATTERNS = [
    {
        "pattern": r"rm\s.*data/",
        "reason": "禁止删除 data/ 目录下的会话数据和报告",
        "fix": "data/ 下的文件是运行时状态，删除会丢失历史会话。如确需清理，请手动确认。",
    },
    {
        "pattern": r"del\s.*data\\",
        "reason": "禁止删除 data/ 目录下的会话数据和报告",
        "fix": "data/ 下的文件是运行时状态，删除会丢失历史会话。如确需清理，请手动确认。",
    },
    {
        "pattern": r"git\s+push.*--force",
        "reason": "禁止 force push",
        "fix": "使用 git push（不带 --force），或先与用户确认。",
    },
    {
        "pattern": r"git\s+reset\s+--hard",
        "reason": "禁止 hard reset",
        "fix": "使用 git stash 或 git checkout 来撤销局部更改。",
    },
    {
        "pattern": r"rm\s.*prompts/",
        "reason": "禁止删除 Prompt 模板文件",
        "fix": "Prompt 是品味编码，删除需要用户确认。当前必需 6 个 Prompt 文件。",
    },
    {
        "pattern": r"rm\s.*state_machine\.py",
        "reason": "禁止删除状态机核心文件",
        "fix": "state_machine.py 是系统骨架，不可删除。",
    },
    {
        "pattern": r"rm\s.*base\.py",
        "reason": "禁止删除 AI Provider 抽象接口",
        "fix": "base.py 定义了所有适配器的契约接口，不可删除。",
    },
]


def check_command(command: str) -> tuple[bool, str]:
    for rule in BLOCKED_PATTERNS:
        if re.search(rule["pattern"], command, re.IGNORECASE):
            return False, f"🚫 {rule['reason']}\nFix: {rule['fix']}"
    return True, ""


def main():
    input_data = json.loads(sys.stdin.read())
    command = input_data.get("command", "")

    is_safe, reason = check_command(command)
    if not is_safe:
        result = {"decision": "block", "reason": reason}
    else:
        result = {"decision": "approve"}

    print(json.dumps(result))


if __name__ == "__main__":
    main()
