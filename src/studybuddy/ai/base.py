"""AI Provider 抽象接口 — 隔离不同 AI API 的差异

通过抽象层，从 Gemini 切到豆包只需替换适配器，
核心逻辑（状态机、Prompt、监控）完全不变。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import AsyncIterator


class AICapability(str, Enum):
    VOICE_IN = "voice_in"
    VOICE_OUT = "voice_out"
    VISION = "vision"
    TEXT = "text"
    FUNCTION_CALLING = "function_calling"


@dataclass
class VisionFrame:
    """单帧视觉数据"""
    image_bytes: bytes
    mime_type: str = "image/jpeg"
    width: int = 0
    height: int = 0


@dataclass
class AudioChunk:
    """音频数据块"""
    data: bytes
    sample_rate: int = 16000
    channels: int = 1


@dataclass
class AIMessage:
    """AI 回复消息"""
    text: str | None = None
    audio: AudioChunk | None = None
    function_call: FunctionCall | None = None
    is_final: bool = False


@dataclass
class FunctionCall:
    """AI 发起的函数调用"""
    name: str
    arguments: dict


@dataclass
class FunctionResponse:
    """函数调用的返回结果"""
    name: str
    result: dict


class AIProvider(ABC):
    """AI 服务提供者抽象接口"""

    @property
    @abstractmethod
    def capabilities(self) -> set[AICapability]:
        ...

    @abstractmethod
    async def connect(self, system_instruction: str, tools: list[dict] | None = None) -> None:
        """建立连接，注入系统指令和可用工具定义"""
        ...

    @abstractmethod
    async def send_audio(self, chunk: AudioChunk) -> None:
        """发送音频数据"""
        ...

    @abstractmethod
    async def send_vision(self, frame: VisionFrame) -> None:
        """发送视觉帧"""
        ...

    @abstractmethod
    async def send_text(self, text: str) -> None:
        """发送文本消息（如 Orchestrator 的阶段指令）"""
        ...

    @abstractmethod
    async def send_function_response(self, response: FunctionResponse) -> None:
        """返回函数调用结果"""
        ...

    @abstractmethod
    def receive(self) -> AsyncIterator[AIMessage]:
        """接收 AI 回复的异步迭代器"""
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """断开连接"""
        ...

    @abstractmethod
    async def is_connected(self) -> bool:
        ...
