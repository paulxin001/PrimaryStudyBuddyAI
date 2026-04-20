"""FastAPI 服务 — Web API + RTC 回调

核心路由：
1. REST API — 家长端创建计划、查看报告
2. RTC Token — 孩子端获取 RTC 入房凭证
3. RTC 回调 — 接收火山引擎 RTC 服务端事件（Function Call 等）
4. 学习控制 — 开始/停止学习会话
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from pathlib import Path

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from .ai.volcano_provider import VolcanoProvider
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

    ai = VolcanoProvider()
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


# --- RTC Token 生成 ---

class RTCTokenRequest(BaseModel):
    session_id: str
    user_id: str = ""


class RTCTokenResponse(BaseModel):
    app_id: str
    room_id: str
    user_id: str
    token: str


@app.post("/api/rtc/token", response_model=RTCTokenResponse)
async def get_rtc_token(req: RTCTokenRequest):
    """为孩子端生成 RTC 入房 Token

    客户端拿到 Token 后用 RTC SDK 加入房间，
    服务端再通过 OpenAPI 把 AI 智能体也加入同一房间。
    """
    orch = _active_sessions.get(req.session_id)
    if not orch or not orch.sm:
        return HTMLResponse('{"error": "会话不存在"}', status_code=404)

    room_id = f"study_{req.session_id}"
    user_id = req.user_id or f"child_{uuid.uuid4().hex[:8]}"

    token = _generate_rtc_token(
        app_id=config.rtc.rtc_app_id,
        app_key=config.rtc.rtc_app_key,
        room_id=room_id,
        user_id=user_id,
    )

    return RTCTokenResponse(
        app_id=config.rtc.rtc_app_id,
        room_id=room_id,
        user_id=user_id,
        token=token,
    )


# --- 学习控制 ---

class StartStudyRequest(BaseModel):
    session_id: str
    room_id: str
    child_user_id: str


@app.post("/api/study/start")
async def start_study(req: StartStudyRequest):
    """孩子端加入 RTC 房间后调用，启动 AI 监督流程"""
    orch = _active_sessions.get(req.session_id)
    if not orch:
        return {"error": "会话不存在"}

    try:
        await orch.start(room_id=req.room_id, child_user_id=req.child_user_id)
        return {"status": "started", "session_id": req.session_id}
    except Exception as e:
        logger.error("start_study_error", error=str(e))
        return {"error": str(e)}


@app.post("/api/study/stop/{session_id}")
async def stop_study(session_id: str):
    """手动停止学习会话"""
    orch = _active_sessions.get(session_id)
    if not orch:
        return {"error": "会话不存在"}

    await orch.stop()
    return {"status": "stopped"}


# --- RTC 服务端回调 ---

@app.post("/api/rtc/callback")
async def rtc_callback(request: Request):
    """接收火山引擎 RTC 服务端回调

    主要处理：
    - FunctionCall: AI 发起的函数调用
    - ConversationStateChanged: 对话状态变化
    """
    body = await request.json()
    event_type = body.get("EventType", "")
    room_id = body.get("RoomId", "")
    task_id = body.get("TaskId", "")

    logger.info("rtc_callback", event_type=event_type, room_id=room_id)

    orch = _find_orchestrator_by_room(room_id)
    if not orch:
        logger.warning("rtc_callback_no_session", room_id=room_id)
        return {"code": 0}

    from .ai.volcano_provider import VolcanoProvider
    if isinstance(orch.ai, VolcanoProvider):
        await orch.ai.handle_rtc_callback(body)

    return {"code": 0}


# --- WebSocket: 状态推送通道 ---

from fastapi import WebSocket, WebSocketDisconnect  # noqa: E402


@app.websocket("/ws/status/{session_id}")
async def status_websocket(ws: WebSocket, session_id: str):
    """WebSocket 用于推送状态更新到客户端

    RTC 模式下音视频不再走 WebSocket，
    此通道仅用于推送状态（当前阶段、计时器、提醒次数等）。
    """
    await ws.accept()

    orch = _active_sessions.get(session_id)
    if not orch:
        await ws.send_json({"type": "error", "message": "会话不存在"})
        await ws.close()
        return

    logger.info("status_ws_connected", session_id=session_id)

    async def send_status(status: dict):
        try:
            await ws.send_json({"type": "status", **status})
        except Exception:
            pass

    orch.on_client_status(send_status)

    try:
        while True:
            data = await ws.receive_json()
            if data.get("type") == "ping":
                await ws.send_json({"type": "pong"})
    except WebSocketDisconnect:
        logger.info("status_ws_disconnected", session_id=session_id)
    except Exception as e:
        logger.error("status_ws_error", session_id=session_id, error=str(e))


# --- 辅助函数 ---

def _find_orchestrator_by_room(room_id: str) -> Orchestrator | None:
    """通过 room_id 查找对应的 Orchestrator"""
    for orch in _active_sessions.values():
        if orch._room_id == room_id:
            return orch
    return None


def _generate_rtc_token(
    app_id: str, app_key: str, room_id: str, user_id: str,
    expire_seconds: int = 3600,
) -> str:
    """生成 RTC 入房 Token（HMAC-SHA256 签名）

    简化版 Token 生成。生产环境建议使用火山引擎官方 SDK。
    """
    timestamp = int(time.time())
    nonce = uuid.uuid4().hex
    expire_at = timestamp + expire_seconds

    payload = f"{app_id}{room_id}{user_id}{nonce}{expire_at}"
    signature = hmac.new(
        app_key.encode(), payload.encode(), hashlib.sha256
    ).hexdigest()

    token_parts = {
        "app_id": app_id,
        "room_id": room_id,
        "user_id": user_id,
        "nonce": nonce,
        "expire_at": expire_at,
        "signature": signature,
    }

    import base64
    return base64.urlsafe_b64encode(
        json.dumps(token_parts).encode()
    ).decode()
