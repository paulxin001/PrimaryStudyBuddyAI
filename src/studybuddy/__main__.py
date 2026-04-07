"""StudyBuddyAI CLI 统一入口

用法:
    python -m studybuddy server              # 启动服务
    python -m studybuddy server --json       # JSON 输出模式
    python -m studybuddy health              # 健康检查
    python -m studybuddy plan "数学:练习册32页 语文:抄写生字"  # 生成计划预览
"""

from __future__ import annotations

import argparse
import json
import sys

import uvicorn


def main():
    parser = argparse.ArgumentParser(
        prog="studybuddy",
        description="StudyBuddyAI — AI 作业监督系统",
    )
    parser.add_argument("--json", action="store_true", help="JSON 格式输出")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("server", help="启动 Web 服务")
    sub.add_parser("health", help="健康检查")

    plan_parser = sub.add_parser("plan", help="预览学习计划")
    plan_parser.add_argument("homework", help="作业内容（每行一科）")
    plan_parser.add_argument("--name", default="小朋友", help="孩子姓名")

    args = parser.parse_args()

    if args.command == "server":
        _run_server(args)
    elif args.command == "health":
        _health_check(args)
    elif args.command == "plan":
        _preview_plan(args)
    else:
        parser.print_help()


def _run_server(args):
    from .config.settings import config
    uvicorn.run(
        "studybuddy.server:app",
        host=config.server.host,
        port=config.server.port,
        reload=config.server.debug,
    )


def _health_check(args):
    from .config.settings import config
    result = {
        "status": "ok",
        "gemini_configured": bool(config.gemini.api_key),
        "volcengine_configured": bool(config.volcengine.access_key),
    }
    if args.json:
        print(json.dumps(result))
    else:
        for k, v in result.items():
            print(f"  {k}: {v}")


def _preview_plan(args):
    from .planner.homework_parser import parse_homework_text
    from .planner.schedule_generator import generate_plan, format_plan_display

    homework_text = args.homework.replace("\\n", "\n")
    tasks = parse_homework_text(homework_text)
    plan = generate_plan(child_name=args.name, tasks=tasks)

    if args.json:
        print(json.dumps({
            "session_id": plan.session_id,
            "child_name": plan.child_name,
            "tasks": [
                {"subject": t.subject, "description": t.description,
                 "duration_minutes": t.duration_minutes}
                for t in plan.tasks
            ],
            "total_minutes": plan.total_minutes,
        }, ensure_ascii=False, indent=2))
    else:
        print(format_plan_display(plan))


if __name__ == "__main__":
    main()
