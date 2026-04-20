# 项目地图 — StudyBuddyAI

## 架构概览

```
家长 Web → [POST /api/plan] → Orchestrator → 生成 StudyPlan
                                    ↓
孩子手机 → [RTC SDK 加入房间] → 火山引擎 RTC 云端
                                    ↓
                    ┌───────────────┼───────────────┐
                    ↓               ↓               ↓
              ASR(语音识别)    TTS(语音合成)    抽帧截图(视觉)
                    ↓               ↑               ↓
                    └───────→ LLM(Seed 2.0 Lite) ←──┘
                              ↓ Function Call
                         Orchestrator
                    ┌────────┼────────┐
                    ↓        ↓        ↓
              StateMachine  Timer  BehaviorAnalyzer
              (7状态流转)  (背压门控) (防误判聚合)
                    ↓
              ReportGenerator → 家长查看
```

Orchestrator 通过火山引擎 OpenAPI（`StartVoiceChat` / `UpdateVoiceChat` / `StopVoiceChat`）管理 AI 会话，
不直接处理音视频流——音视频由 RTC SDK 和火山引擎云端负责。

## 目录树

```
StudyBuddyAI/
├── AGENTS.md                          # 入口 (本文件的上级)
├── .cursor/
│   ├── rules/                         # Cursor Rules（自动注入约束）
│   │   ├── project-guide.mdc          #   全局：架构红线 + 编码风格
│   │   ├── state-machine.mdc          #   状态机模块修改约束
│   │   ├── prompts.mdc                #   Prompt 模板修改约束
│   │   └── ai-provider.mdc            #   AI 适配器修改约束
│   ├── hooks/                         # 守卫脚本（操作拦截）
│   │   ├── guard_shell.py             #   危险命令拦截
│   │   └── guard_edit.py              #   核心文件保护
│   └── hooks.json                     # Hooks 配置
├── docs/
│   ├── PROJECT_MAP.md                 # 架构图（本文件）
│   ├── VOICE_ARCH.md                  # 语音交互架构决策（RTC 三段式 + LLM 选型）
│   ├── MILESTONES.md                  # 里程碑
│   └── AUTONOMY_RULES.md             # 自主性规则
├── scripts/
│   └── lint_invariants.py             # 不变量检查器（13项约束）
├── src/studybuddy/
│   ├── __main__.py                    # CLI 入口
│   ├── server.py                      # FastAPI 服务
│   ├── orchestrator/
│   │   ├── engine.py                  # 编排引擎（大脑）
│   │   ├── state_machine.py           # 状态机 + 数据模型
│   │   └── timer.py                   # 计时器管理
│   ├── ai/
│   │   ├── base.py                    # AI Provider 抽象接口
│   │   └── volcano_provider.py        # 火山引擎 RTC 对话式 AI 适配器
│   ├── prompts/                       # Prompt 模板（6个 Markdown）
│   ├── planner/                       # 作业解析 + 计划生成
│   ├── monitor/                       # 行为分析
│   ├── reporter/                      # 报告生成
│   ├── client/web/                    # PWA Web 客户端
│   └── config/                        # 配置
├── data/                              # 运行时数据（不入git）
└── requirements.txt
```

## 数据流

1. **家长输入** → `homework_parser.py` → `TaskItem[]`
2. **TaskItem[]** → `schedule_generator.py` → `StudyPlan`
3. **StudyPlan** → `Orchestrator.create_session()` → `SessionContext`
4. **孩子连接** → RTC SDK 加入房间 → `Orchestrator.start()` → `StartVoiceChat`
5. **语音交互** → RTC 云端：ASR → LLM(Seed 2.0 Lite) → TTS → 孩子端扬声器
6. **视觉监督** → RTC 云端：摄像头抽帧 → LLM 多模态理解 → Function Call
7. **AI Function Call** → `Orchestrator._handle_function_call()` → 状态转换
8. **Orchestrator 主动控制** → `UpdateVoiceChat`：Prompt 切换 / 主动播报 / 打断
9. **全部完成** → `StopVoiceChat` → `ReportGenerator` → JSON 报告 → 家长 Web 查看

## 状态机转换表

| 当前状态 | 事件 | 下一状态 |
|---------|------|---------|
| PlanReady | child_connected | TaskBriefing |
| TaskBriefing | briefing_done | Studying |
| Studying | study_timer_up | BreakTime |
| Studying | task_completed_early | BreakTime |
| Studying | attention_lost | Nudge |
| Nudge | attention_regained | Studying |
| BreakTime | has_more_tasks | TaskBriefing |
| BreakTime | all_tasks_finished | AllDone |
| AllDone | report_generated | ReportSent |
