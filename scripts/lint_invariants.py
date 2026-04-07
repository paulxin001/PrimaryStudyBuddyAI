"""项目不变量检查器 — 机械化执行

检查项目的关键约束是否满足，输出带修复指令的错误信息。
可作为 CI 步骤或手动运行。

用法: python scripts/lint_invariants.py [project_root]
"""

import re
import sys
from pathlib import Path


class InvariantChecker:
    def __init__(self, root: Path):
        self.root = root
        self.errors: list[str] = []

    def check_agents_md(self, max_lines: int = 100):
        agents = self.root / "AGENTS.md"
        if not agents.exists():
            self.errors.append(
                "Error: AGENTS.md not found.\n"
                "Fix: Create AGENTS.md as the AI entry point. "
                "See .cursor/skills/harness-engineering/templates.md"
            )
            return
        lines = agents.read_text(encoding="utf-8").splitlines()
        if len(lines) > max_lines:
            self.errors.append(
                f"Error: AGENTS.md has {len(lines)} lines (max {max_lines}).\n"
                f"Fix: Extract detailed content to docs/ and keep AGENTS.md as directory."
            )

    def check_no_hardcoded_keys(self):
        key_pattern = re.compile(
            r"""(?:api_key|secret_key|access_key|token)\s*=\s*["'][A-Za-z0-9_\-]{20,}["']""",
            re.IGNORECASE,
        )
        for py_file in self.root.rglob("*.py"):
            if any(skip in str(py_file) for skip in [".venv", "node_modules", "__pycache__", "lint_invariants"]):
                continue
            try:
                content = py_file.read_text(encoding="utf-8")
                for match in key_pattern.finditer(content):
                    line = content[:match.start()].count("\n") + 1
                    self.errors.append(
                        f"Error: Possible hardcoded secret at {py_file.relative_to(self.root)}:{line}\n"
                        f"Fix: Use environment variables. See .env.example"
                    )
                    break
            except Exception:
                pass

    def check_prompt_files_exist(self):
        required = [
            "system_persona.md", "task_briefing.md", "studying_monitor.md",
            "nudge_templates.md", "break_time.md", "report_summary.md",
        ]
        prompts_dir = self.root / "src" / "studybuddy" / "prompts"
        for name in required:
            if not (prompts_dir / name).exists():
                self.errors.append(
                    f"Error: Required prompt template missing: prompts/{name}\n"
                    f"Fix: Create {prompts_dir / name} with appropriate content."
                )

    def check_state_machine_integrity(self):
        sm_file = self.root / "src" / "studybuddy" / "orchestrator" / "state_machine.py"
        if not sm_file.exists():
            self.errors.append(
                "Error: state_machine.py not found.\n"
                "Fix: This is a core file. Check orchestrator/ directory."
            )
            return
        content = sm_file.read_text(encoding="utf-8")
        required_states = ["PLAN_READY", "TASK_BRIEFING", "STUDYING", "NUDGE", "BREAK_TIME", "ALL_DONE", "REPORT_SENT"]
        for state in required_states:
            if state not in content:
                self.errors.append(
                    f"Error: Required state {state} missing from state_machine.py\n"
                    f"Fix: All 7 states must be defined. See docs/PROJECT_MAP.md"
                )

    def check_file_sizes(self, max_lines: int = 500):
        for py_file in self.root.rglob("*.py"):
            if any(skip in str(py_file) for skip in [".venv", "node_modules", "__pycache__"]):
                continue
            try:
                lines = py_file.read_text(encoding="utf-8").splitlines()
                if len(lines) > max_lines:
                    self.errors.append(
                        f"Error: {py_file.relative_to(self.root)} has {len(lines)} lines (max {max_lines}).\n"
                        f"Fix: Split into smaller modules. Extract types, service logic separately."
                    )
            except Exception:
                pass

    def check_no_answer_in_prompts(self):
        """确保 Prompt 中没有帮孩子做作业的指令"""
        prompts_dir = self.root / "src" / "studybuddy" / "prompts"
        forbidden = ["直接告诉答案", "帮他做", "给出答案", "告诉他答案"]
        for md_file in prompts_dir.glob("*.md"):
            try:
                content = md_file.read_text(encoding="utf-8")
                for phrase in forbidden:
                    if phrase in content:
                        self.errors.append(
                            f"Error: Forbidden phrase '{phrase}' found in {md_file.name}\n"
                            f"Fix: Remove answer-giving instructions. AI should guide, not give answers."
                        )
            except Exception:
                pass

    def run_all(self) -> int:
        self.check_agents_md()
        self.check_no_hardcoded_keys()
        self.check_prompt_files_exist()
        self.check_state_machine_integrity()
        self.check_file_sizes()
        self.check_no_answer_in_prompts()

        if self.errors:
            for err in self.errors:
                print(f"\n{err}")
            print(f"\n{'=' * 60}")
            print(f"Found {len(self.errors)} invariant violation(s).")
            return len(self.errors)
        else:
            print("All invariants satisfied.")
            return 0


if __name__ == "__main__":
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
    checker = InvariantChecker(root)
    sys.exit(min(checker.run_all(), 1))
