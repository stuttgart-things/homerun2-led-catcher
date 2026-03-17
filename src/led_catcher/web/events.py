"""Event tracker — ring buffer for recent LED display events."""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass


@dataclass
class LedEvent:
    """A recorded LED display event."""

    timestamp: str = ""
    severity: str = ""
    system: str = ""
    title: str = ""
    author: str = ""
    kind: str = ""
    message: str = ""
    color: tuple[int, int, int] = (255, 255, 255)

    def severity_css(self) -> str:
        return {
            "error": "severity-error",
            "warning": "severity-warning",
            "success": "severity-success",
            "info": "severity-info",
            "debug": "severity-debug",
        }.get(self.severity.lower(), "severity-info")

    def color_hex(self) -> str:
        return f"#{self.color[0]:02x}{self.color[1]:02x}{self.color[2]:02x}"


class EventTracker:
    """Thread-safe ring buffer of recent LED events."""

    def __init__(self, max_events: int = 100) -> None:
        self._events: deque[LedEvent] = deque(maxlen=max_events)
        self._lock = threading.Lock()
        self._version = 0

    def record(self, event: LedEvent) -> None:
        with self._lock:
            self._events.appendleft(event)
            self._version += 1

    def recent(self, n: int = 20) -> list[LedEvent]:
        with self._lock:
            return list(self._events)[:n]

    @property
    def total(self) -> int:
        with self._lock:
            return len(self._events)

    @property
    def version(self) -> int:
        with self._lock:
            return self._version
