"""项目不变量检查器 — 机械化执行

检查项目的关键约束是否满足，输出带修复指令的错误信息。
可作为 CI 步骤或手动运行。

用法: python scripts/lint_invariants.py [project_root]

不变量清单（13 项）:
  L1 文档: AGENTS.md 存在且 ≤100 行
  L2 文档: docs/ 下必需文件存在且被 AGENTS.md 引用
  L3 安全: 无硬编码密钥
  L4 Prompt: 6 个必需模板存在
  L5 Prompt: 无帮孩子做作业的指令
  L6 Prompt: 占位符与 engine.py 对齐
  L7 状态机: 7 个状态完整
  L8 状态机: TRANSITIONS 表条目数 = 9
  L9 架构: 模块依赖方向正确（ai/ 不反向依赖 orchestrator/）
  L10 架构: Function Calling 工具名两侧对齐
  L11 品味: 单文件不超过 500 行
  L12 品味: 公开函数有类型注解（抽样检查）
  L13 Harness: .cursor/rules/ 下有项目级规则
"""

import ast
import re
import sys
from pathlib import Path


SKIP_DIRS = {".venv", "node_modules", "__pycache__", ".git", ".cursor"}


class InvariantChecker:
    def __init__(self, root: Path):
        self.root = root
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def _should_skip(self, path: Path) -> bool:
        return any(skip in path.parts for skip in SKIP_DIRS)

    # --- L1: AGENTS.md ---

    def check_agents_md(self, max_lines: int = 100):
        agents = self.root / "AGENTS.md"
        if not agents.exists():
            self.errors.append(
                "Error [L1]: AGENTS.md not found.\n"
                "Fix: Create AGENTS.md as the AI entry point. "
                "See .cursor/skills/harness-engineering/templates.md"
            )
            return
        lines = agents.read_text(encoding="utf-8").splitlines()
        if len(lines) > max_lines:
            self.errors.append(
                f"Error [L1]: AGENTS.md has {len(lines)} lines (max {max_lines}).\n"
                f"Fix: Extract detailed content to docs/ and keep AGENTS.md as directory."
            )

    # --- L2: docs/ 必需文件 ---

    def check_docs_exist(self):
        required_docs = {
            "docs/PROJECT_MAP.md": "架构图 + 数据流",
            "docs/VOICE_ARCH.md": "语音交互技术架构决策",
            "docs/AUTONOMY_RULES.md": "自主性边界规则",
        }
        for path, desc in required_docs.items():
            full = self.root / path
            if not full.exists():
                self.errors.append(
                    f"Error [L2]: Required doc missing: {path} ({desc})\n"
                    f"Fix: Create {path}. See AGENTS.md '深入了解' section."
                )

    # --- L3: 无硬编码密钥 ---

    def check_no_hardcoded_keys(self):
        key_pattern = re.compile(
            r"""(?:api_key|secret_key|access_key|token)\s*=\s*["'][A-Za-z0-9_\-]{20,}["']""",
            re.IGNORECASE,
        )
        for py_file in self.root.rglob("*.py"):
            if self._should_skip(py_file) or "lint_invariants" in py_file.name:
                continue
            try:
                content = py_file.read_text(encoding="utf-8")
                for match in key_pattern.finditer(content):
                    line = content[:match.start()].count("\n") + 1
                    self.errors.append(
                        f"Error [L3]: Possible hardcoded secret at "
                        f"{py_file.relative_to(self.root)}:{line}\n"
                        f"Fix: Use environment variables. See .env.example"
                    )
                    break
            except Exception:
                pass

    # --- L4: Prompt 模板完整性 ---

    def check_prompt_files_exist(self):
        required = [
            "system_persona.md", "task_briefing.md", "studying_monitor.md",
            "nudge_templates.md", "break_time.md", "report_summary.md",
        ]
        prompts_dir = self.root / "src" / "studybuddy" / "prompts"
        for name in required:
            if not (prompts_dir / name).exists():
                self.errors.append(
                    f"Error [L4]: Required prompt template missing: prompts/{name}\n"
                    f"Fix: Create {prompts_dir / name} with appropriate content."
                )

    # --- L5: Prompt 安全内容 ---

    def check_no_answer_in_prompts(self):
        prompts_dir = self.root / "src" / "studybuddy" / "prompts"
        forbidden = ["直接告诉答案", "帮他做", "给出答案", "告诉他答案", "把答案", "代替他做"]
        for md_file in prompts_dir.glob("*.md"):
            try:
                content = md_file.read_text(encoding="utf-8")
                for phrase in forbidden:
                    if phrase in content:
                        self.errors.append(
                            f"Error [L5]: Forbidden phrase '{phrase}' found in {md_file.name}\n"
                            f"Fix: Remove answer-giving instructions. AI should guide, not give answers."
                        )
            except Exception:
                pass

    # --- L6: Prompt 占位符与 engine.py 对齐 ---

    def check_prompt_placeholders(self):
        engine_file = self.root / "src" / "studybuddy" / "orchestrator" / "engine.py"
        if not engine_file.exists():
            return
        engine_content = engine_file.read_text(encoding="utf-8")
        prompts_dir = self.root / "src" / "studybuddy" / "prompts"

        blocks = re.findall(
            r'_load_prompt\("(\w+\.md)"\)\.format\((.*?)\)',
            engine_content,
            re.DOTALL,
        )
        for filename, args_block in blocks:
            prompt_file = prompts_dir / filename
            if not prompt_file.exists():
                continue
            prompt_content = prompt_file.read_text(encoding="utf-8")
            kwarg_names = re.findall(r'^\s*(\w+)\s*=', args_block, re.MULTILINE)
            for arg in kwarg_names:
                placeholder = "{" + arg + "}"
                if placeholder not in prompt_content:
                    self.warnings.append(
                        f"Warning [L6]: engine.py passes '{arg}' to {filename}, "
                        f"but placeholder {placeholder} not found in prompt.\n"
                        f"Fix: Add {placeholder} to prompts/{filename} or "
                        f"remove the argument from engine.py."
                    )

    # --- L7: 状态机 7 状态完整 ---

    def check_state_machine_integrity(self):
        sm_file = self.root / "src" / "studybuddy" / "orchestrator" / "state_machine.py"
        if not sm_file.exists():
            self.errors.append(
                "Error [L7]: state_machine.py not found.\n"
                "Fix: This is a core file. Check orchestrator/ directory."
            )
            return
        content = sm_file.read_text(encoding="utf-8")
        required_states = [
            "PLAN_READY", "TASK_BRIEFING", "STUDYING", "NUDGE",
            "BREAK_TIME", "ALL_DONE", "REPORT_SENT",
        ]
        for state in required_states:
            if state not in content:
                self.errors.append(
                    f"Error [L7]: Required state {state} missing from state_machine.py\n"
                    f"Fix: All 7 states must be defined. See docs/PROJECT_MAP.md"
                )

    # --- L8: TRANSITIONS 表条目数 ---

    def check_transitions_count(self, expected: int = 9):
        sm_file = self.root / "src" / "studybuddy" / "orchestrator" / "state_machine.py"
        if not sm_file.exists():
            return
        content = sm_file.read_text(encoding="utf-8")
        transition_entries = re.findall(
            r"\(State\.\w+,\s*Event\.\w+\):\s*State\.\w+",
            content,
        )
        actual = len(transition_entries)
        if actual != expected:
            self.errors.append(
                f"Error [L8]: TRANSITIONS table has {actual} entries (expected {expected}).\n"
                f"Fix: Check state_machine.py TRANSITIONS dict. "
                f"Adding/removing transitions changes system behavior — needs user confirmation."
            )

    # --- L9: 模块依赖方向 ---

    def check_dependency_direction(self):
        ai_dir = self.root / "src" / "studybuddy" / "ai"
        if not ai_dir.exists():
            return
        for py_file in ai_dir.rglob("*.py"):
            if self._should_skip(py_file):
                continue
            try:
                content = py_file.read_text(encoding="utf-8")
                if "from ..orchestrator" in content or "import orchestrator" in content:
                    self.errors.append(
                        f"Error [L9]: {py_file.relative_to(self.root)} imports from orchestrator/\n"
                        f"Fix: ai/ must not depend on orchestrator/. "
                        f"Dependency direction is: orchestrator → ai, not the reverse.\n"
                        f"See .cursor/rules/project-guide.mdc for dependency graph."
                    )
            except Exception:
                pass

        planner_dir = self.root / "src" / "studybuddy" / "planner"
        if not planner_dir.exists():
            return
        for py_file in planner_dir.rglob("*.py"):
            if self._should_skip(py_file):
                continue
            try:
                content = py_file.read_text(encoding="utf-8")
                if "from ..ai" in content or "import ai" in content:
                    self.errors.append(
                        f"Error [L9]: {py_file.relative_to(self.root)} imports from ai/\n"
                        f"Fix: planner/ must not depend on ai/. "
                        f"Planner is pure logic, no AI dependency."
                    )
            except Exception:
                pass

    # --- L10: Function Calling 工具名对齐 ---

    def check_function_calling_alignment(self):
        engine = self.root / "src" / "studybuddy" / "orchestrator" / "engine.py"
        provider = self.root / "src" / "studybuddy" / "ai" / "volcano_provider.py"
        if not engine.exists() or not provider.exists():
            return

        engine_content = engine.read_text(encoding="utf-8")
        engine_handlers = set(re.findall(r'fc\.name\s*==\s*"(\w+)"', engine_content))

        provider_content = provider.read_text(encoding="utf-8")
        provider_tools = set(re.findall(r'"name":\s*"(\w+)"', provider_content))

        for tool in provider_tools:
            if tool not in engine_handlers:
                self.errors.append(
                    f"Error [L10]: Tool '{tool}' declared in volcano_provider.py "
                    f"but no handler in engine.py\n"
                    f"Fix: Add handler for '{tool}' in engine.py._handle_function_call() "
                    f"or remove from STUDY_TOOLS."
                )
        for handler in engine_handlers:
            if handler not in provider_tools:
                self.warnings.append(
                    f"Warning [L10]: Handler for '{handler}' in engine.py "
                    f"but no tool declaration in volcano_provider.py\n"
                    f"Fix: Ensure all AI providers declare this tool."
                )

    # --- L11: 文件大小 ---

    def check_file_sizes(self, max_lines: int = 500):
        for py_file in self.root.rglob("*.py"):
            if self._should_skip(py_file):
                continue
            try:
                lines = py_file.read_text(encoding="utf-8").splitlines()
                if len(lines) > max_lines:
                    self.errors.append(
                        f"Error [L11]: {py_file.relative_to(self.root)} has {len(lines)} lines "
                        f"(max {max_lines}).\n"
                        f"Fix: Split into smaller modules. Extract types to types.py, "
                        f"service logic to separate files."
                    )
            except Exception:
                pass

    # --- L12: 公开函数类型注解（抽样） ---

    def check_type_annotations(self):
        core_files = [
            "src/studybuddy/orchestrator/state_machine.py",
            "src/studybuddy/ai/base.py",
            "src/studybuddy/planner/homework_parser.py",
            "src/studybuddy/planner/schedule_generator.py",
        ]
        for rel_path in core_files:
            full = self.root / rel_path
            if not full.exists():
                continue
            try:
                tree = ast.parse(full.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        continue
                    if node.name.startswith("_"):
                        continue
                    if node.returns is None:
                        self.warnings.append(
                            f"Warning [L12]: Public function '{node.name}' in {rel_path} "
                            f"has no return type annotation.\n"
                            f"Fix: Add return type annotation for agent readability."
                        )
            except Exception:
                pass

    # --- L13: Harness 基础设施 ---

    def check_harness_infrastructure(self):
        rules_dir = self.root / ".cursor" / "rules"
        if not rules_dir.exists() or not list(rules_dir.glob("*.mdc")):
            self.warnings.append(
                "Warning [L13]: No .cursor/rules/*.mdc files found.\n"
                "Fix: Create project-level rules. See .cursor/skills/harness-engineering/templates.md"
            )

        hooks_file = self.root / ".cursor" / "hooks.json"
        if not hooks_file.exists():
            self.warnings.append(
                "Warning [L13]: .cursor/hooks.json not found.\n"
                "Fix: Create hooks.json with guard scripts for dangerous operations."
            )

    # --- 执行入口 ---

    def run_all(self) -> int:
        self.check_agents_md()
        self.check_docs_exist()
        self.check_no_hardcoded_keys()
        self.check_prompt_files_exist()
        self.check_no_answer_in_prompts()
        self.check_prompt_placeholders()
        self.check_state_machine_integrity()
        self.check_transitions_count()
        self.check_dependency_direction()
        self.check_function_calling_alignment()
        self.check_file_sizes()
        self.check_type_annotations()
        self.check_harness_infrastructure()

        if self.warnings:
            print("\n--- Warnings ---")
            for w in self.warnings:
                print(f"\n{w}")

        if self.errors:
            print("\n--- Errors ---")
            for err in self.errors:
                print(f"\n{err}")
            print(f"\n{'=' * 60}")
            print(f"Found {len(self.errors)} error(s), {len(self.warnings)} warning(s).")
            return len(self.errors)
        else:
            total_checks = 13
            print(f"\n{'=' * 60}")
            print(f"All {total_checks} invariants satisfied. {len(self.warnings)} warning(s).")
            return 0


if __name__ == "__main__":
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
    checker = InvariantChecker(root)
    sys.exit(min(checker.run_all(), 1))
