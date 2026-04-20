"""全局配置 — 环境变量 + 默认值"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = PROJECT_ROOT / "data"
SESSIONS_DIR = DATA_DIR / "sessions"
REPORTS_DIR = DATA_DIR / "reports"
LOGS_DIR = DATA_DIR / "logs"
PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


class RTCConfig(BaseModel):
    """火山引擎 RTC 对话式 AI 配置"""
    access_key: str = os.getenv("VOLC_ACCESS_KEY", "")
    secret_key: str = os.getenv("VOLC_SECRET_KEY", "")
    rtc_app_id: str = os.getenv("VOLC_RTC_APP_ID", "")
    rtc_app_key: str = os.getenv("VOLC_RTC_APP_KEY", "")
    api_host: str = os.getenv("VOLC_RTC_API_HOST", "rtc.volcengineapi.com")
    api_version: str = "2024-12-01"
    region: str = os.getenv("VOLC_REGION", "cn-north-1")


class ASRConfig(BaseModel):
    """语音识别配置"""
    app_id: str = os.getenv("VOLC_ASR_APP_ID", "")
    mode: str = "bigmodel"
    cluster: str = "volcengine_streaming_common"
    silence_time: int = 600


class LLMConfig(BaseModel):
    """大语言模型配置"""
    mode: str = "ArkV3"
    endpoint_id: str = os.getenv("VOLC_LLM_ENDPOINT_ID", "")
    temperature: float = 0.7
    max_tokens: int = 512
    history_length: int = 15
    vision_enable: bool = True


class TTSConfig(BaseModel):
    """语音合成配置"""
    app_id: str = os.getenv("VOLC_TTS_APP_ID", "")
    token: str = os.getenv("VOLC_TTS_TOKEN", "")
    cluster: str = "volcano_tts"
    voice_type: str = os.getenv("VOLC_TTS_VOICE", "BV700_streaming")
    speed_ratio: float = 1.1
    volume_ratio: float = 1.0
    emotion: str = "happy"
    emotion_strength: float = 0.6


class ServerConfig(BaseModel):
    host: str = os.getenv("HOST", "0.0.0.0")
    port: int = int(os.getenv("PORT", "8000"))
    debug: bool = os.getenv("DEBUG", "true").lower() == "true"


class TimerDefaults(BaseModel):
    """默认的时间配置"""
    task_briefing_seconds: int = 30
    default_study_minutes: int = 25
    default_break_minutes: int = 5
    long_break_minutes: int = 15
    nudge_cooldown_seconds: int = 60
    attention_check_interval_seconds: int = 15


class AppConfig(BaseModel):
    rtc: RTCConfig = RTCConfig()
    asr: ASRConfig = ASRConfig()
    llm: LLMConfig = LLMConfig()
    tts: TTSConfig = TTSConfig()
    server: ServerConfig = ServerConfig()
    timer: TimerDefaults = TimerDefaults()


config = AppConfig()
