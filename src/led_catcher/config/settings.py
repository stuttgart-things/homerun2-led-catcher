"""Configuration loaded from environment variables."""

from __future__ import annotations

import logging
import os
import socket
import sys
from dataclasses import dataclass, field


@dataclass
class RedisConfig:
    addr: str = "localhost"
    port: int = 6379
    password: str = ""
    streams: list[str] = field(default_factory=lambda: ["messages"])

    @property
    def stream(self) -> str:
        """Legacy single-stream accessor — returns the first configured stream."""
        return self.streams[0] if self.streams else "messages"


@dataclass
class Config:
    redis: RedisConfig
    consumer_group: str = "homerun2-led-catcher"
    consumer_name: str = ""
    led_mode: str = "full"  # led, web, full
    health_port: int = 8080
    profile_path: str = "profile.yaml"
    log_format: str = "json"
    log_level: str = "info"
    version: str = "dev"
    commit: str = "unknown"
    date: str = "unknown"


def _getenv(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def parse_streams(streams_env: str, stream_fallback: str) -> list[str]:
    """Resolve the list of Redis streams to subscribe to.

    Precedence:
      1. REDIS_STREAMS — comma-separated, whitespace-trimmed, empty entries dropped
      2. REDIS_STREAM  — legacy single-stream env var, wrapped in a one-element list
      3. ["messages"]  — hardcoded default

    Legacy single-stream deployments keep working unchanged.
    """
    if streams_env:
        parts = [p.strip() for p in streams_env.split(",")]
        parts = [p for p in parts if p]
        if parts:
            return parts
    if stream_fallback:
        return [stream_fallback]
    return ["messages"]


def load_config() -> Config:
    redis_cfg = RedisConfig(
        addr=_getenv("REDIS_ADDR", "localhost"),
        port=int(_getenv("REDIS_PORT", "6379")),
        password=_getenv("REDIS_PASSWORD", ""),
        streams=parse_streams(_getenv("REDIS_STREAMS", ""), _getenv("REDIS_STREAM", "")),
    )

    consumer_name = _getenv("CONSUMER_NAME", "")
    if not consumer_name:
        consumer_name = socket.gethostname()

    return Config(
        redis=redis_cfg,
        consumer_group=_getenv("CONSUMER_GROUP", "homerun2-led-catcher"),
        consumer_name=consumer_name,
        led_mode=_getenv("LED_MODE", "full"),
        health_port=int(_getenv("HEALTH_PORT", "8080")),
        profile_path=_getenv("PROFILE_PATH", "profile.yaml"),
        log_format=_getenv("LOG_FORMAT", "json"),
        log_level=_getenv("LOG_LEVEL", "info"),
        version=_getenv("VERSION", "dev"),
        commit=_getenv("COMMIT", "unknown"),
        date=_getenv("DATE", "unknown"),
    )


def setup_logging(cfg: Config) -> None:
    level_map = {
        "debug": logging.DEBUG,
        "info": logging.INFO,
        "warning": logging.WARNING,
        "warn": logging.WARNING,
        "error": logging.ERROR,
    }
    level = level_map.get(cfg.log_level.lower(), logging.INFO)

    if cfg.log_format == "json":
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(_JsonFormatter())
    else:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)


class _JsonFormatter(logging.Formatter):
    """Structured JSON log formatter matching Go slog output style."""

    def format(self, record: logging.LogRecord) -> str:
        import json
        from datetime import datetime, timezone

        log_entry = {
            "time": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "msg": record.getMessage(),
            "logger": record.name,
        }
        # Include extra fields if any
        for key in (
            "version",
            "commit",
            "date",
            "redis_addr",
            "stream",
            "streams",
            "consumer_group",
            "error",
            "object_id",
            "stream_id",
            "severity",
            "system",
            "title",
            "author",
        ):
            val = getattr(record, key, None)
            if val is not None:
                log_entry[key] = val
        return json.dumps(log_entry)
