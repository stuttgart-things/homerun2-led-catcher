"""Web handler — records caught messages as LED events for the HTMX simulator."""

from __future__ import annotations

import logging
from datetime import datetime

from led_catcher.models import CaughtMessage
from led_catcher.profile import Profile, match_rule
from led_catcher.web.events import EventTracker, LedEvent

logger = logging.getLogger(__name__)


def create_web_handler(profile: Profile, tracker: EventTracker):
    """Create a web handler closure that records events for the simulator."""

    def web_handler(msg: CaughtMessage) -> None:
        config = match_rule(profile, msg.message)

        color = (0, 100, 255)  # default info blue
        kind = "text"
        if config is not None:
            color = config.color
            kind = config.kind

        event = LedEvent(
            timestamp=datetime.now().strftime("%H:%M:%S"),
            severity=msg.message.severity,
            system=msg.message.system,
            title=msg.message.title or msg.message.message,
            author=msg.message.author,
            kind=kind,
            message=msg.message.message,
            color=color,
        )
        tracker.record(event)
        logger.debug("web event recorded: %s %s", msg.message.system, msg.message.severity)

    return web_handler
