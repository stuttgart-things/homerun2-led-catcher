"""Web handler — records caught messages as LED events for the HTMX simulator."""

from __future__ import annotations

import logging
from dataclasses import asdict, replace
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


def _localised(config: DisplayConfig | None) -> DisplayConfig | None:
    """`config` with an https image swapped for its cached file name, as
    DisplayWorker._localise does for the panel.

    A rule can take its picture from the message (#148), and the message names
    a URL. The canvas asks /api/preview/image for a bare file name only, so
    without this it got a 400 and drew its static-text fallback: "---" in the
    severity's colour. An overlay whose picture cannot be fetched gives way to
    what it played over, as on the panel; any other display keeps its URL and
    falls back to text.

    Blocking, on the consumer's loop, for the first download of each URL only:
    fetch_image answers from the cache after that. Done here rather than on a
    thread so the events stay in the order they came, and the figure is on the
    timeline before the next point.
    """
    from led_catcher.display.remote import RemoteImageError, fetch_image, is_remote

    image = str(getattr(config, "image", "") or "")
    if config is None or not is_remote(image):
        return config
    try:
        name = fetch_image(image)
    except RemoteImageError as err:
        logger.warning("image not shown in the simulator, fetching %s failed: %s", image, err)
        return config.then if config.overlay else config
    return replace(config, image=name)


def build_event(profile: Profile, msg: Message, config: DisplayConfig | None) -> LedEvent:
    """The timeline event for `msg`, drawn as `config` says, or unmatched."""
    config = _localised(config)
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
        # What an overlay gives way to, played by the canvas once the overlay
        # is over and nothing newer waits (DisplayWorker._show_then).
        then=build_event(profile, msg, config.then) if config is not None and config.then is not None else None,
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
            worker.submit(config, system=msg.system, title=msg.title)
        logger.info("replaying event %d: system=%s severity=%s", event_id, msg.system, msg.severity)
        return event

    return replay


def _card_json(config) -> dict | None:
    """The card as the panel lays it out, for the canvas.

    ``content`` is what the card says before it is cut to 64 columns: the
    big-screen view (/wall) lays the card out again for its own size.
    """
    from led_catcher.display.card import card_layout, content_of
    from led_catcher.web.preview import PANEL_WIDTH

    try:
        content = content_of(config)
        return {**card_layout(content, config.color, PANEL_WIDTH).to_json(), "content": asdict(content)}
    except Exception:
        # The simulator falls back to static text; the event is still recorded.
        logger.exception("cannot lay out card for the simulator")
        return None
