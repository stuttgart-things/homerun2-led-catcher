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
        text = font = image = ""
        card = None
        if config is not None:
            color = config.color
            kind = config.kind
            duration = config.duration
            hold = config.hold
            text, font, image = config.text, config.font, config.image
            if kind.lower() == "card":
                card = _card_json(config)
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
            text=text,
            font=font or "6x10.bdf",
            image=image,
            card=card,
            matched=config is not None,
        )
        tracker.record(event)
        logger.debug("web event recorded: %s %s", msg.message.system, msg.message.severity)

    return web_handler


def _card_json(config) -> dict | None:
    """The card as the panel lays it out, for the canvas."""
    from led_catcher.display.card import card_layout, content_of
    from led_catcher.web.preview import PANEL_WIDTH

    try:
        return card_layout(content_of(config), config.color, PANEL_WIDTH).to_json()
    except Exception:
        # The simulator falls back to static text; the event is still recorded.
        logger.exception("cannot lay out card for the simulator")
        return None
