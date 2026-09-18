"""Event tracker — ring buffer for recent LED display events."""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field


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
    # How long the matched rule wants this on the panel. Carried through to the
    # browser so the simulated matrix can show what the real one shows —
    # without them it had its own hardcoded five seconds and disagreed with the
    # hardware (#62).
    duration: float = 5.0
    hold: bool = False
    # What the panel draws, so the canvas can draw the same (#83): the rule's
    # rendered text (not the message title), and the font and image it names.
    text: str = ""
    font: str = "6x10.bdf"
    image: str = ""
    # False when no rule matched: the panel shows nothing for it, and neither
    # should the canvas. The timeline still lists it.
    matched: bool = True
    # Wall-clock time of the event, so a page opened later can tell whether a
    # finite display is still up.
    at: float = field(default_factory=time.time)
    # Assigned by EventTracker.record — a sequence the browser can follow
    # without guessing from timestamps.
    id: int = 0

    def severity_css(self) -> str:
        return {
            "error": "severity-error",
            "critical": "severity-error",
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
            self._version += 1
            event.id = self._version
            self._events.appendleft(event)

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
