"""Gemini Live API 适配器

处理 session 续期（2分钟视频会话限制）和上下文压缩，
对上层完全透明。
"""

from __future__ import annotations

import asyncio
import base64
from typing import AsyncIterator

import structlog

from .base import (
    AICapability,
    AIMessage,
    AIProvider,
    AudioChunk,
    FunctionCall,
    FunctionResponse,
    VisionFrame,
)
from ..config.settings import config

logger = structlog.get_logger()

try:
    from google import genai
    from google.genai import types

    HAS_GENAI = True
except ImportError:
    HAS_GENAI = False
    logger.warning("google-genai not installed, GeminiLiveProvider will not work")

STUDY_TOOLS = [
    {
        "name": "report_status",
        "description": "向编排器报告当前观察到的学习状态",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": [
                        "focused",
                        "distracted",
                        "bad_posture",
                        "playing_with_pen",
                        "looking_away",
                        "task_seems_done",
                        "child_asking_question",
                        "child_left_seat",
                    ],
                    "description": "观察到的状态",
                },
                "confidence": {
                    "type": "number",
                    "description": "置信度 0-1",
                },
                "detail": {
                    "type": "string",
                    "description": "具体描述",
                },
            },
            "required": ["status", "confidence"],
        },
    },
    {
        "name": "request_phase_change",
        "description": "请求切换到下一个阶段（如当前任务已完成）",
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "请求原因",
                },
            },
            "required": ["reason"],
        },
    },
]


class GeminiLiveProvider(AIProvider):
    """Google Gemini Live API 适配器

    自动处理:
    - ContextWindowCompression（滑动窗口）
    - SessionResumption（跨连接恢复）
    """

    def __init__(self):
        if not HAS_GENAI:
            raise RuntimeError("google-genai package is required")
        self._client = genai.Client(api_key=config.gemini.api_key)
        self._session = None
        self._resume_handle: str | None = None
        self._system_instruction: str = ""
        self._tools: list[dict] | None = None
        self._connected = False
        self._receive_queue: asyncio.Queue[AIMessage] = asyncio.Queue()
        self._receive_task: asyncio.Task | None = None

    @property
    def capabilities(self) -> set[AICapability]:
        return {
            AICapability.VOICE_IN,
            AICapability.VOICE_OUT,
            AICapability.VISION,
            AICapability.TEXT,
            AICapability.FUNCTION_CALLING,
        }

    async def connect(self, system_instruction: str, tools: list[dict] | None = None) -> None:
        self._system_instruction = system_instruction
        self._tools = tools or STUDY_TOOLS
        await self._establish_session()

    async def _establish_session(self) -> None:
        """建立或恢复 Gemini Live 会话"""
        live_config = types.LiveConnectConfig(
            response_modalities=["AUDIO", "TEXT"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=config.gemini.voice,
                    )
                )
            ),
            system_instruction=types.Content(
                parts=[types.Part(text=self._system_instruction)]
            ),
            context_window_compression=types.ContextWindowCompressionConfig(
                sliding_window=types.SlidingWindow(),
            ),
            session_resumption=types.SessionResumptionConfig(
                handle=self._resume_handle,
            ),
            tools=self._build_tool_declarations(),
        )

        self._session = await self._client.aio.live.connect(
            model=config.gemini.model,
            config=live_config,
        )
        self._connected = True
        self._receive_task = asyncio.create_task(self._receive_loop())
        logger.info("gemini_session_established", resumed=self._resume_handle is not None)

    def _build_tool_declarations(self) -> list:
        if not self._tools:
            return []
        declarations = []
        for tool in self._tools:
            declarations.append(types.Tool(
                function_declarations=[
                    types.FunctionDeclaration(
                        name=tool["name"],
                        description=tool["description"],
                        parameters=tool.get("parameters"),
                    )
                ]
            ))
        return declarations

    async def _receive_loop(self) -> None:
        """后台持续接收 Gemini 消息，推入队列"""
        try:
            while self._connected and self._session:
                async for msg in self._session.receive():
                    if hasattr(msg, "session_resumption_update"):
                        update = msg.session_resumption_update
                        if update and hasattr(update, "handle"):
                            self._resume_handle = update.handle
                            logger.debug("session_resume_handle_updated")
                        continue

                    ai_msg = self._parse_message(msg)
                    if ai_msg:
                        await self._receive_queue.put(ai_msg)
        except Exception as e:
            logger.error("gemini_receive_error", error=str(e))
            if self._connected:
                await self._reconnect()

    async def _reconnect(self) -> None:
        """自动重连（session resumption）"""
        logger.info("gemini_reconnecting", has_handle=self._resume_handle is not None)
        try:
            if self._session:
                await self._session.__aexit__(None, None, None)
        except Exception:
            pass
        await asyncio.sleep(1)
        await self._establish_session()

    def _parse_message(self, msg) -> AIMessage | None:
        """解析 Gemini Live API 消息为统一的 AIMessage"""
        if not hasattr(msg, "server_content") and not hasattr(msg, "tool_call"):
            return None

        if hasattr(msg, "tool_call") and msg.tool_call:
            for fc in msg.tool_call.function_calls:
                return AIMessage(
                    function_call=FunctionCall(
                        name=fc.name,
                        arguments=dict(fc.args) if fc.args else {},
                    )
                )

        if hasattr(msg, "server_content") and msg.server_content:
            content = msg.server_content
            text = None
            audio = None

            if hasattr(content, "model_turn") and content.model_turn:
                for part in content.model_turn.parts:
                    if hasattr(part, "text") and part.text:
                        text = part.text
                    if hasattr(part, "inline_data") and part.inline_data:
                        audio = AudioChunk(
                            data=part.inline_data.data,
                            sample_rate=24000,
                        )

            is_final = getattr(content, "turn_complete", False)
            if text or audio:
                return AIMessage(text=text, audio=audio, is_final=is_final)

        return None

    async def send_audio(self, chunk: AudioChunk) -> None:
        if self._session:
            await self._session.send_realtime_input(audio=chunk.data)

    async def send_vision(self, frame: VisionFrame) -> None:
        if self._session:
            encoded = base64.b64encode(frame.image_bytes).decode("utf-8")
            await self._session.send_realtime_input(
                media=types.Blob(
                    data=encoded,
                    mime_type=frame.mime_type,
                )
            )

    async def send_text(self, text: str) -> None:
        if self._session:
            await self._session.send_client_content(
                turns=types.Content(
                    role="user",
                    parts=[types.Part(text=text)],
                )
            )

    async def send_function_response(self, response: FunctionResponse) -> None:
        if self._session:
            await self._session.send_tool_response(
                function_responses=[
                    types.FunctionResponse(
                        name=response.name,
                        response=response.result,
                    )
                ]
            )

    async def receive(self) -> AsyncIterator[AIMessage]:
        while self._connected:
            try:
                msg = await asyncio.wait_for(self._receive_queue.get(), timeout=1.0)
                yield msg
            except asyncio.TimeoutError:
                continue

    async def disconnect(self) -> None:
        self._connected = False
        if self._receive_task and not self._receive_task.done():
            self._receive_task.cancel()
        if self._session:
            try:
                await self._session.__aexit__(None, None, None)
            except Exception:
                pass
        logger.info("gemini_session_disconnected")

    async def is_connected(self) -> bool:
        return self._connected
