# AGENTS.md — StudyBuddyAI 入口

AI 作业监督系统：家长远程设置，AI 自主按计划陪伴孩子写作业。

**项目地图：** `docs/PROJECT_MAP.md`

---

## 命令速查

```bash
python -m studybuddy server              # 启动服务
python -m studybuddy health --json       # 健康检查
python -m studybuddy plan "数学:口算题卡\n语文:抄写生字" --name 小明  # 预览计划
python scripts/lint_invariants.py        # 不变量检查（13项）
```

> 所有命令加 `--json` 输出结构化摘要。修改代码后务必跑 `lint_invariants.py`。

---

## 任务路由

| 用户说 | 做什么 |
|--------|--------|
| 「启动服务」 | `python -m studybuddy server` |
| 「生成计划」 | POST /api/plan |
| 「查看报告」 | GET /api/report/{session_id} |
| 「改 Prompt」 | 编辑 `src/studybuddy/prompts/*.md` (**需确认**) |
| 「加新 AI 接入」 | 继承 `ai/base.py:AIProvider`，参考 `docs/VOICE_ARCH.md` |
| 「适配小智机器人」 | 开发 `client/xiaozhi/adapter.py` |

---

## 核心模块

| 模块 | 路径 | 职责 |
|------|------|------|
| 状态机 | `orchestrator/state_machine.py` | 7状态流转 + 事件驱动 |
| 编排器 | `orchestrator/engine.py` | 驱动状态机 + AI会话管理 |
| AI抽象层 | `ai/base.py` | Provider接口，隔离API差异 |
| RTC适配 | `ai/volcano_provider.py` | 火山引擎 RTC 对话式 AI（ASR+LLM+TTS） |
| Prompt模板 | `prompts/*.md` | 各阶段AI指令（版本化管理） |
| 计划生成 | `planner/` | 作业解析 + 时间规划 |
| 行为分析 | `monitor/` | 滑动窗口聚合 + 防误判 |
| 报告 | `reporter/` | 报告生成 + 家长通知 |

---

## 自主性速查

| 操作 | 等级 | 可自主 | 需确认 |
|------|------|--------|--------|
| 启动/重启服务 | 🟢 | 运行命令 | - |
| 生成计划 | 🟢 | 调用API | - |
| 修改 Prompt | 🔴 | - | 所有修改 |
| 修改状态机 | 🔴 | - | 所有修改 |
| 修改 AI 适配器 | 🟡 | bug修复 | 接口变更 |

### 全局红线

- ❌ 不修改状态机转换规则未经确认
- ❌ 不硬编码 API Key
- ❌ 不删除 data/ 下的会话数据
- ❌ 不在 Prompt 中包含帮孩子做作业的指令

---

## Harness 约束体系

| 组件 | 路径 | 作用 |
|------|------|------|
| 项目规则 | `.cursor/rules/project-guide.mdc` | 全局编码约束，自动注入 |
| 模块规则 | `.cursor/rules/state-machine.mdc` | 状态机修改约束 |
| 模块规则 | `.cursor/rules/prompts.mdc` | Prompt 修改约束 |
| 模块规则 | `.cursor/rules/ai-provider.mdc` | AI 适配器约束 |
| 守卫脚本 | `.cursor/hooks/guard_shell.py` | 拦截危险 shell 命令 |
| 守卫脚本 | `.cursor/hooks/guard_edit.py` | 保护核心文件修改需确认 |
| 不变量 | `scripts/lint_invariants.py` | 13 项机械化约束检查 |

---

## 深入了解

| 主题 | 文件 |
|------|------|
| 项目地图 | `docs/PROJECT_MAP.md` |
| 语音交互架构 | `docs/VOICE_ARCH.md` |
| 自主性边界 | `docs/AUTONOMY_RULES.md` |
| 里程碑 | `docs/MILESTONES.md` |
