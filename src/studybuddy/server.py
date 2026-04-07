"""FastAPI 服务 — Web API + WebSocket 音视频中继

两个核心路由：
1. REST API — 家长端创建计划、查看报告
2. WebSocket — 孩子端实时音视频流
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import structlog
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .ai.gemini_live import GeminiLiveProvider
from .config.settings import config, REPORTS_DIR
from .orchestrator.engine import Orchestrator
from .orchestrator.state_machine import SessionContext
from .planner.homework_parser import parse_homework_text
from .planner.schedule_generator import generate_plan, format_plan_display
from .reporter.report_generator import format_report_for_parent

logger = structlog.get_logger()

app = FastAPI(title="StudyBuddyAI", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

CLIENT_DIR = Path(__file__).parent / "client" / "web"

_active_sessions: dict[str, Orchestrator] = {}


# --- 静态文件 ---

@app.get("/")
async def index():
    return FileResponse(CLIENT_DIR / "index.html")


@app.get("/study")
async def study_page():
    return FileResponse(CLIENT_DIR / "study.html")


@app.get("/report/{session_id}")
async def report_page(session_id: str):
    return FileResponse(CLIENT_DIR / "report.html")


@app.get("/static/{filename}")
async def static_file(filename: str):
    path = CLIENT_DIR / filename
    if path.exists():
        return FileResponse(path)
    return HTMLResponse("Not Found", status_code=404)


# --- REST API: 家长端 ---

class HomeworkInput(BaseModel):
    child_name: str = "小朋友"
    homework_text: str


class PlanResponse(BaseModel):
    session_id: str
    display: str
    tasks: list[dict]
    total_minutes: int


@app.post("/api/plan", response_model=PlanResponse)
async def create_plan(req: HomeworkInput):
    """家长提交作业，生成学习计划"""
    tasks = parse_homework_text(req.homework_text)
    plan = generate_plan(child_name=req.child_name, tasks=tasks)

    ai = GeminiLiveProvider()
    orch = Orchestrator(ai)
    await orch.create_session(plan)
    _active_sessions[plan.session_id] = orch

    logger.info("plan_created", session_id=plan.session_id, tasks=len(tasks))

    return PlanResponse(
        session_id=plan.session_id,
        display=format_plan_display(plan),
        tasks=[
            {"subject": t.subject, "description": t.description,
             "duration_minutes": t.duration_minutes}
            for t in plan.tasks
        ],
        total_minutes=plan.total_minutes,
    )


@app.get("/api/sessions")
async def list_sessions():
    """列出活跃会话"""
    return {
        "sessions": [
            {
                "session_id": sid,
                "state": orch.sm.state.value if orch.sm else "unknown",
                "child_name": orch.sm.ctx.plan.child_name if orch.sm else "",
            }
            for sid, orch in _active_sessions.items()
        ]
    }


@app.get("/api/report/{session_id}")
async def get_report(session_id: str):
    """获取学习报告"""
    report_path = REPORTS_DIR / f"{session_id}_report.json"
    if not report_path.exists():
        return {"error": "报告尚未生成"}
    report = json.loads(report_path.read_text(encoding="utf-8"))
    return {
        "report": report,
        "display": format_report_for_parent(report),
    }


# --- WebSocket: 孩子端实时连接 ---

@app.websocket("/ws/study/{session_id}")
async def study_websocket(ws: WebSocket, session_id: str):
    """孩子端 WebSocket 连接

    协议：
    - 客户端发送: {"type": "audio", "data": "<base64>"} | {"type": "video", "data": "<base64>"}
    - 服务端发送: {"type": "audio", "data": "<base64>"} | {"type": "status", ...}
    """
    await ws.accept()

    orch = _active_sessions.get(session_id)
    if not orch:
        await ws.send_json({"type": "error", "message": "会话不存在"})
        await ws.close()
        return

    logger.info("child_connected", session_id=session_id)

    import base64

    async def send_audio_to_client(audio_data: bytes):
        try:
            await ws.send_json({
                "type": "audio",
                "data": base64.b64encode(audio_data).decode(),
            })
        except Exception:
            pass

    async def send_status_to_client(status: dict):
        try:
            await ws.send_json({"type": "status", **status})
        except Exception:
            pass

    orch.on_client_audio(send_audio_to_client)
    orch.on_client_status(send_status_to_client)

    try:
        await orch.start()

        while True:
            data = await ws.receive_json()
            msg_type = data.get("type")

            if msg_type == "audio":
                audio_bytes = base64.b64decode(data["data"])
                await orch.handle_client_audio(audio_bytes)

            elif msg_type == "video":
                frame_bytes = base64.b64decode(data["data"])
                await orch.handle_client_video_frame(frame_bytes)

    except WebSocketDisconnect:
        logger.info("child_disconnected", session_id=session_id)
    except Exception as e:
        logger.error("websocket_error", session_id=session_id, error=str(e))
    finally:
        await orch.stop()
