"""火山引擎 RTC 对话式 AI 适配器

通过服务端 OpenAPI（StartVoiceChat / UpdateVoiceChat / StopVoiceChat）
管理 AI 会话。音视频流由 RTC SDK（客户端）和火山引擎云端直接处理，
服务端不经手音视频数据。

架构详见 docs/VOICE_ARCH.md。
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from typing import AsyncIterator
from urllib.parse import quote

import httpx
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

STUDY_TOOLS = [
    {
        "type": "function",
        "name": "report_status",
        "description": "向编排器报告当前观察到的学习状态，约每15-30秒上报一次",
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
        "type": "function",
        "name": "request_phase_change",
        "description": "当判断当前任务已完成时，请求切换到下一阶段",
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "请求原因，如 task_completed / child_requested_stop",
                },
            },
            "required": ["reason"],
        },
    },
]


class VolcanoProvider(AIProvider):
    """火山引擎 RTC 对话式 AI 适配器

    工作模式：
    - 音视频流: 客户端 RTC SDK ↔ 火山引擎云端（ASR/LLM/TTS）
    - 会话管理: 服务端通过 OpenAPI 控制（本类）
    - Function Call: 通过 RTC 服务端回调接收，通过 UpdateVoiceChat 回传结果

    详见 docs/VOICE_ARCH.md。
    """

    def __init__(self) -> None:
        self._room_id: str = ""
        self._task_id: str = ""
        self._connected = False
        self._system_instruction: str = ""
        self._receive_queue: asyncio.Queue[AIMessage] = asyncio.Queue()
        self._http: httpx.AsyncClient | None = None

    @property
    def capabilities(self) -> set[AICapability]:
        return {
            AICapability.VOICE_IN,
            AICapability.VOICE_OUT,
            AICapability.VISION,
            AICapability.TEXT,
            AICapability.FUNCTION_CALLING,
        }

    async def connect(
        self, system_instruction: str, tools: list[dict] | None = None
    ) -> None:
        self._system_instruction = system_instruction
        self._http = httpx.AsyncClient(timeout=30.0)
        logger.info("volcano_provider_ready")

    async def start_voice_chat(
        self, room_id: str, task_id: str, child_user_id: str,
        agent_user_id: str = "ai_buddy",
        welcome_message: str = "",
    ) -> dict:
        """调用 StartVoiceChat 在 RTC 房间中创建 AI 智能体"""
        self._room_id = room_id
        self._task_id = task_id

        body = {
            "AppId": config.rtc.rtc_app_id,
            "RoomId": room_id,
            "TaskId": task_id,
            "AgentConfig": {
                "TargetUserId": [child_user_id],
                "UserId": agent_user_id,
                "WelcomeMessage": welcome_message,
                "EnableConversationStateCallback": True,
                "AnsMode": 3,
            },
            "Config": self._build_config(),
        }

        result = await self._call_api("StartVoiceChat", body)
        self._connected = True
        logger.info(
            "voice_chat_started",
            room_id=room_id,
            task_id=task_id,
        )
        return result

    async def stop_voice_chat(self) -> dict:
        """调用 StopVoiceChat 结束 AI 会话"""
        if not self._connected:
            return {}

        body = {
            "AppId": config.rtc.rtc_app_id,
            "RoomId": self._room_id,
            "TaskId": self._task_id,
        }
        result = await self._call_api("StopVoiceChat", body)
        self._connected = False
        logger.info("voice_chat_stopped", room_id=self._room_id)
        return result

    async def update_voice_chat(
        self, command: str, message: str = "", interrupt_mode: int = 0
    ) -> dict:
        """调用 UpdateVoiceChat 更新会话"""
        body: dict = {
            "AppId": config.rtc.rtc_app_id,
            "RoomId": self._room_id,
            "TaskId": self._task_id,
            "Command": command,
        }
        if message:
            body["Message"] = message
        if interrupt_mode:
            body["InterruptMode"] = interrupt_mode

        return await self._call_api("UpdateVoiceChat", body)

    # --- AIProvider 抽象接口实现 ---

    async def send_audio(self, chunk: AudioChunk) -> None:
        """RTC 模式下服务端不直接处理音频，由客户端 RTC SDK 负责"""

    async def send_vision(self, frame: VisionFrame) -> None:
        """RTC 模式下视觉由云端 VisionConfig 自动处理抽帧截图"""

    async def send_text(self, text: str) -> None:
        """通过 ExternalTextToSpeech 向孩子端播报文本"""
        if not self._connected:
            return
        await self.update_voice_chat(
            command="ExternalTextToSpeech",
            message=text[:200],
            interrupt_mode=2,
        )

    async def send_function_response(self, response: FunctionResponse) -> None:
        """通过 FunctionCallResult 回传 Function Call 结果"""
        if not self._connected:
            return
        result_json = json.dumps(response.result, ensure_ascii=False)
        await self.update_voice_chat(
            command="FunctionCallResult",
            message=result_json,
        )

    async def receive(self) -> AsyncIterator[AIMessage]:
        """接收 AI 消息（Function Call 通过 RTC 回调推入队列）"""
        while self._connected:
            try:
                msg = await asyncio.wait_for(
                    self._receive_queue.get(), timeout=1.0
                )
                yield msg
            except asyncio.TimeoutError:
                continue

    async def disconnect(self) -> None:
        if self._connected:
            await self.stop_voice_chat()
        if self._http:
            await self._http.aclose()
            self._http = None
        logger.info("volcano_provider_disconnected")

    async def is_connected(self) -> bool:
        return self._connected

    # --- RTC 回调处理 ---

    async def handle_rtc_callback(self, event: dict) -> None:
        """处理来自 RTC 服务端回调的事件

        RTC 对话式 AI 通过服务端回调通知 Function Call、
        对话状态变化等。此方法由 server.py 的回调路由调用。
        """
        event_type = event.get("EventType", "")

        if event_type == "FunctionCall":
            data = event.get("Data", {})
            name = data.get("Name", "")
            arguments_str = data.get("Arguments", "{}")
            try:
                arguments = json.loads(arguments_str)
            except json.JSONDecodeError:
                arguments = {}
            await self._receive_queue.put(
                AIMessage(function_call=FunctionCall(name=name, arguments=arguments))
            )

        elif event_type == "ConversationStateChanged":
            state = event.get("Data", {}).get("State", "")
            logger.debug("rtc_conversation_state", state=state)

    # --- 内部方法 ---

    def _build_config(self) -> dict:
        """构建 StartVoiceChat 的 Config 参数"""
        return {
            "ASRConfig": {
                "Provider": "volcano",
                "ProviderParams": {
                    "AppId": config.asr.app_id,
                    "Mode": config.asr.mode,
                    "Cluster": config.asr.cluster,
                },
                "VADConfig": {"SilenceTime": config.asr.silence_time},
            },
            "LLMConfig": {
                "Mode": config.llm.mode,
                "EndPointId": config.llm.endpoint_id,
                "SystemMessages": [self._system_instruction],
                "Temperature": config.llm.temperature,
                "MaxTokens": config.llm.max_tokens,
                "HistoryLength": config.llm.history_length,
                "VisionConfig": {
                    "Enable": config.llm.vision_enable,
                },
            },
            "TTSConfig": {
                "Provider": "volcano",
                "ProviderParams": {
                    "app": {
                        "appid": config.tts.app_id,
                        "token": config.tts.token,
                        "cluster": config.tts.cluster,
                    },
                    "audio": {
                        "voice_type": config.tts.voice_type,
                        "speed_ratio": config.tts.speed_ratio,
                        "volume_ratio": config.tts.volume_ratio,
                        "emotion": config.tts.emotion,
                        "emotion_strength": config.tts.emotion_strength,
                    },
                },
            },
            "InterruptMode": 0,
        }

    async def _call_api(self, action: str, body: dict) -> dict:
        """调用火山引擎 RTC OpenAPI（V4 签名）"""
        if not self._http:
            raise RuntimeError("HTTP client not initialized, call connect() first")

        now = datetime.now(timezone.utc)
        date_str = now.strftime("%Y%m%dT%H%M%SZ")
        date_short = now.strftime("%Y%m%d")

        query_params = {
            "Action": action,
            "Version": config.rtc.api_version,
        }
        query_string = "&".join(
            f"{quote(k, safe='')}={quote(v, safe='')}"
            for k, v in sorted(query_params.items())
        )
        url = f"https://{config.rtc.api_host}/?{query_string}"

        payload = json.dumps(body, ensure_ascii=False)
        payload_hash = hashlib.sha256(payload.encode()).hexdigest()

        headers = {
            "Content-Type": "application/json",
            "X-Date": date_str,
            "Host": config.rtc.api_host,
        }

        signed_headers = "content-type;host;x-date"
        canonical_headers = (
            f"content-type:application/json\n"
            f"host:{config.rtc.api_host}\n"
            f"x-date:{date_str}\n"
        )
        canonical_request = (
            f"POST\n"
            f"/\n"
            f"{query_string}\n"
            f"{canonical_headers}\n"
            f"{signed_headers}\n"
            f"{payload_hash}"
        )

        credential_scope = f"{date_short}/{config.rtc.region}/rtc/request"
        string_to_sign = (
            f"HMAC-SHA256\n"
            f"{date_str}\n"
            f"{credential_scope}\n"
            f"{hashlib.sha256(canonical_request.encode()).hexdigest()}"
        )

        def _hmac_sha256(key: bytes, msg: str) -> bytes:
            return hmac.new(key, msg.encode(), hashlib.sha256).digest()

        k_date = _hmac_sha256(config.rtc.secret_key.encode(), date_short)
        k_region = _hmac_sha256(k_date, config.rtc.region)
        k_service = _hmac_sha256(k_region, "rtc")
        k_signing = _hmac_sha256(k_service, "request")

        signature = hmac.new(
            k_signing, string_to_sign.encode(), hashlib.sha256
        ).hexdigest()

        auth = (
            f"HMAC-SHA256 "
            f"Credential={config.rtc.access_key}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, "
            f"Signature={signature}"
        )
        headers["Authorization"] = auth

        resp = await self._http.post(url, content=payload, headers=headers)
        result = resp.json()

        if "Error" in result.get("ResponseMetadata", {}):
            error = result["ResponseMetadata"]["Error"]
            logger.error(
                "rtc_api_error",
                action=action,
                code=error.get("Code"),
                message=error.get("Message"),
            )
            raise RuntimeError(
                f"RTC API error: {error.get('Code')} - {error.get('Message')}"
            )

        logger.debug("rtc_api_ok", action=action)
        return result.get("Result", {})
