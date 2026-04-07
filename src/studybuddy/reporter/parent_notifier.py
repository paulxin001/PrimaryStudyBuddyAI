"""家长通知推送 — MVP 阶段仅提供 Web 查看"""

from __future__ import annotations

import structlog

logger = structlog.get_logger()


async def notify_parent(session_id: str, report: dict) -> None:
    """通知家长查看报告

    MVP 阶段：仅记录日志，家长通过 Web 页面主动查看。
    后续迭代可接入：
    - 微信公众号/小程序推送
    - 邮件通知
    - 短信通知
    """
    logger.info(
        "parent_notification",
        session_id=session_id,
        child_name=report.get("child_name"),
        rating=report.get("overview", {}).get("overall_rating"),
    )
