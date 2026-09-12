"""Configuration loaded from environment variables."""

from __future__ import annotations

import logging
import os
import re
import socket
import sys
from dataclasses import dataclass, field

DEFAULT_REDIS_STARTUP_TIMEOUT = 120.0
"""Seconds the consumer retries Redis at startup before the process exits (#65)."""


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
    ui_stream_presets: list[list[str]] = field(default_factory=list)
    redis_startup_timeout: float = DEFAULT_REDIS_STARTUP_TIMEOUT
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


def parse_stream_presets(presets_env: str, fallback: list[str]) -> list[list[str]]:
    """Resolve the one-click stream presets offered by the web simulator.

    Grammar: presets are comma-separated, and the streams within one preset are
    ``|``-separated. ``messages,tabletennis|scale`` yields two buttons — one
    subscribing to ``messages``, one subscribing to ``tabletennis`` and ``scale``
    together.

    Whitespace is trimmed, empty entries are dropped, and duplicate presets are
    collapsed. Falls back to a single preset holding the configured streams, which
    is a button that does nothing useful — acceptable, because the panel is then
    already in its normal state.
    """
    presets: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    for entry in presets_env.split(","):
        group = [s.strip() for s in entry.split("|")]
        group = [s for s in group if s]
        if not group or tuple(group) in seen:
            continue
        seen.add(tuple(group))
        presets.append(group)

    if presets:
        return presets

    configured = [s.strip() for s in fallback if s.strip()]
    return [configured] if configured else []


_DURATION_PART = re.compile(r"(\d+(?:\.\d*)?|\.\d+)(ns|us|µs|ms|s|m|h)")
_DURATION_UNITS = {"ns": 1e-9, "us": 1e-6, "µs": 1e-6, "ms": 1e-3, "s": 1.0, "m": 60.0, "h": 3600.0}


def parse_duration(value: str) -> float:
    """Parse a Go-style duration (``90s``, ``2m``, ``1m30s``, ``500ms``) into seconds.

    Same syntax as Go's ``time.ParseDuration``, so ``REDIS_STARTUP_TIMEOUT`` reads
    the same here as in the Go homerun2 services. A bare number has no unit and is
    rejected, as in Go.
    """
    text = value.strip()
    sign = 1.0
    if text[:1] in ("+", "-"):
        sign = -1.0 if text[0] == "-" else 1.0
        text = text[1:]
    if not text:
        raise ValueError(f"invalid duration {value!r}")
    if text == "0":
        return 0.0
    total = 0.0
    pos = 0
    for match in _DURATION_PART.finditer(text):
        if match.start() != pos:
            raise ValueError(f"invalid duration {value!r}")
        total += float(match.group(1)) * _DURATION_UNITS[match.group(2)]
        pos = match.end()
    if pos != len(text) or pos == 0:
        raise ValueError(f"invalid duration {value!r}")
    return sign * total


def parse_redis_startup_timeout(raw: str) -> float:
    """``REDIS_STARTUP_TIMEOUT`` in seconds; unset means the default.

    An unparsable, zero or negative value raises instead of falling back: a typo
    should fail startup, not quietly restore a budget nobody chose.
    """
    if not raw.strip():
        return DEFAULT_REDIS_STARTUP_TIMEOUT
    try:
        seconds = parse_duration(raw)
    except ValueError as exc:
        raise ValueError(f"REDIS_STARTUP_TIMEOUT {raw!r}: {exc}") from None
    if seconds <= 0:
        raise ValueError(f"REDIS_STARTUP_TIMEOUT {raw!r}: must be positive")
    return seconds


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
        ui_stream_presets=parse_stream_presets(_getenv("UI_STREAM_PRESETS", ""), redis_cfg.streams),
        redis_startup_timeout=parse_redis_startup_timeout(_getenv("REDIS_STARTUP_TIMEOUT", "")),
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
            "attempt",
            "attempts",
            "next_sleep",
            "redis_startup_timeout",
        ):
            val = getattr(record, key, None)
            if val is not None:
                log_entry[key] = val
        return json.dumps(log_entry)
