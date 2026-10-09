"""Web handler — records caught messages as LED events for the HTMX simulator."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Callable

from led_catcher.models import CaughtMessage, Message
from led_catcher.profile import DisplayConfig, Profile, fallback_card, match_rule
from led_catcher.web.events import EventTracker, LedEvent

logger = logging.getLogger(__name__)

Replayer = Callable[[int], "LedEvent | None"]
"""Shows the timeline event with this id again; None when there is none to show."""


def create_web_handler(profile: Profile, tracker: EventTracker):
    """Create a web handler closure that records events for the simulator."""

    def web_handler(msg: CaughtMessage) -> None:
        config = match_rule(profile, msg.message)
        if config is None:
            logger.info(
                "no matching display rule for system=%s severity=%s — recorded uncoloured by rule",
                msg.message.system,
                msg.message.severity,
            )
        tracker.record(build_event(profile, msg.message, config))
        logger.debug("web event recorded: %s %s", msg.message.system, msg.message.severity)

    return web_handler


def build_event(profile: Profile, msg: Message, config: DisplayConfig | None) -> LedEvent:
    """The timeline event for `msg`, drawn as `config` says, or unmatched."""
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
        color = profile.colors.get(msg.severity.lower(), (255, 255, 255))

    return LedEvent(
        timestamp=datetime.now().strftime("%H:%M:%S"),
        severity=msg.severity,
        system=msg.system,
        title=msg.title or msg.message,
        author=msg.author,
        kind=kind,
        message=msg.message,
        color=color,
        duration=duration,
        hold=hold,
        text=text,
        font=font or "6x10.bdf",
        image=image,
        card=card,
        matched=config is not None,
        source=msg,
    )


def create_replayer(profile: Profile, tracker: EventTracker, worker=None) -> Replayer:
    """Show a timeline event again, on request (#128).

    The event is matched as when it arrived, but without quiet hours: a click is
    a deliberate request. One no rule shows becomes a card in its severity
    colour. The replay is recorded as a new event, so every open simulator
    draws it, and with a `worker` (led, full) it goes on the panel too.
    """

    def replay(event_id: int) -> LedEvent | None:
        original = tracker.get(event_id)
        if original is None or original.source is None:
            return None
        msg = original.source
        config = match_rule(profile, msg, ignore_quiet_hours=True) or fallback_card(profile, msg)
        event = build_event(profile, msg, config)
        event.replay_of = original.replay_of or original.id
        tracker.record(event)
        if worker is not None:
            worker.submit(config)
        logger.info("replaying event %d: system=%s severity=%s", event_id, msg.system, msg.severity)
        return event

    return replay


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
