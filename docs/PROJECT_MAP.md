# 项目地图 — StudyBuddyAI

## 架构概览

```
家长 Web → [POST /api/plan] → Orchestrator → 生成 StudyPlan
                                    ↓
孩子手机 → [WebSocket /ws/study] → Orchestrator
                                    ↓
                             StateMachine (7状态)
                                    ↓
                    ┌───────────────┼───────────────┐
                    ↓               ↓               ↓
              TimerManager    GeminiLive API   BehaviorAnalyzer
              (背压门控)      (语音+视觉)       (防误判聚合)
                    ↓               ↓               ↓
                    └───────────────┼───────────────┘
                                    ↓
                           ReportGenerator → 家长查看
```

## 目录树

```
StudyBuddyAI/
├── AGENTS.md                          # 入口 (本文件的上级)
├── docs/
│   ├── PROJECT_MAP.md                 # 架构图（本文件）
│   ├── plan.md                        # 设计方案
│   └── AUTONOMY_RULES.md             # 自主性规则
├── src/studybuddy/
│   ├── __main__.py                    # CLI 入口
│   ├── server.py                      # FastAPI 服务
│   ├── orchestrator/
│   │   ├── engine.py                  # 编排引擎（大脑）
│   │   ├── state_machine.py           # 状态机 + 数据模型
│   │   └── timer.py                   # 计时器管理
│   ├── ai/
│   │   ├── base.py                    # AI Provider 抽象接口
│   │   └── gemini_live.py             # Gemini Live API 适配器
│   ├── prompts/                       # Prompt 模板（Markdown）
│   ├── planner/                       # 作业解析 + 计划生成
│   ├── monitor/                       # 行为分析
│   ├── reporter/                      # 报告生成
│   ├── client/web/                    # PWA Web 客户端
│   └── config/                        # 配置
├── data/                              # 运行时数据（不入git）
├── scripts/                           # 工具脚本
└── requirements.txt
```

## 数据流

1. **家长输入** → `homework_parser.py` → `TaskItem[]`
2. **TaskItem[]** → `schedule_generator.py` → `StudyPlan`
3. **StudyPlan** → `Orchestrator.create_session()` → `SessionContext`
4. **孩子连接** → `Orchestrator.start()` → 状态机启动
5. **音视频流** → WebSocket → Gemini Live API ↔ 孩子端
6. **AI function call** → `Orchestrator._handle_function_call()` → 状态转换
7. **全部完成** → `ReportGenerator` → JSON报告 → 家长Web查看

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
