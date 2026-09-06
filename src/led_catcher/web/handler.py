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

        kind = "text"
        duration = 5.0
        hold = False
        if config is not None:
            color = config.color
            kind = config.kind
            duration = config.duration
            hold = config.hold
        else:
            # No rule matched. Falling back to a fixed info blue made an
            # unmatched CRITICAL indistinguishable from an INFO in the
            # simulator — the wrong answer looked like a right one. Use the
            # severity's own colour so a hole in the profile shows as a
            # message that is coloured but never rendered on the matrix.
            logger.info(
                "no matching display rule for system=%s severity=%s — recorded uncoloured by rule",
                msg.message.system,
                msg.message.severity,
            )
            color = profile.colors.get(msg.message.severity.lower(), (255, 255, 255))

        event = LedEvent(
            timestamp=datetime.now().strftime("%H:%M:%S"),
            severity=msg.message.severity,
            system=msg.message.system,
            title=msg.message.title or msg.message.message,
            author=msg.message.author,
            kind=kind,
            message=msg.message.message,
            color=color,
            duration=duration,
            hold=hold,
        )
        tracker.record(event)
        logger.debug("web event recorded: %s %s", msg.message.system, msg.message.severity)

    return web_handler
