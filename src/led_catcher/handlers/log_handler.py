"""Structured log handler — logs every caught message with severity-aware log levels."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from led_catcher.models import CaughtMessage

logger = logging.getLogger("led_catcher.handlers.log")

_SEVERITY_MAP = {
    "error": logging.ERROR,
    "warning": logging.WARNING,
    "warn": logging.WARNING,
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "success": logging.INFO,
}

MAX_MESSAGE_AGE_SECONDS = 30


def log_handler(msg: CaughtMessage) -> None:
    """Log the caught message at the appropriate severity level."""
    severity = msg.message.severity.lower()
    level = _SEVERITY_MAP.get(severity, logging.INFO)

    # Timestamp validation (reject stale messages)
    if msg.message.timestamp:
        try:
            ts = float(msg.message.timestamp)
            age = abs(datetime.now(timezone.utc).timestamp() - ts)
            if age > MAX_MESSAGE_AGE_SECONDS:
                logger.debug(
                    "skipping stale message (age=%.1fs)",
                    age,
                    extra={"object_id": msg.object_id, "stream_id": msg.stream_id},
                )
                return
        except (ValueError, TypeError):
            pass  # non-numeric timestamp, skip validation

    logger.log(
        level,
        "caught: %s",
        msg.message.title or msg.message.message,
        extra={
            "object_id": msg.object_id,
            "stream_id": msg.stream_id,
            "severity": msg.message.severity,
            "system": msg.message.system,
            "title": msg.message.title,
            "author": msg.message.author,
        },
    )
