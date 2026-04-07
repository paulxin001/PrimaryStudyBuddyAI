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


class GeminiConfig(BaseModel):
    api_key: str = os.getenv("GEMINI_API_KEY", "")
    model: str = "gemini-2.5-flash-preview-native-audio-dialog"
    voice: str = "Leda"
    max_session_minutes: int = 10


class VolcEngineConfig(BaseModel):
    access_key: str = os.getenv("VOLCENGINE_ACCESS_KEY", "")
    secret_key: str = os.getenv("VOLCENGINE_SECRET_KEY", "")


class ServerConfig(BaseModel):
    host: str = os.getenv("HOST", "0.0.0.0")
    port: int = int(os.getenv("PORT", "8000"))
    debug: bool = os.getenv("DEBUG", "true").lower() == "true"


class TimerDefaults(BaseModel):
    """默认的时间配置（分钟）"""
    task_briefing_seconds: int = 30
    default_study_minutes: int = 25
    default_break_minutes: int = 5
    long_break_minutes: int = 15
    nudge_cooldown_seconds: int = 60
    attention_check_interval_seconds: int = 15


class AppConfig(BaseModel):
    gemini: GeminiConfig = GeminiConfig()
    volcengine: VolcEngineConfig = VolcEngineConfig()
    server: ServerConfig = ServerConfig()
    timer: TimerDefaults = TimerDefaults()


config = AppConfig()
