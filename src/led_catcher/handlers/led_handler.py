"""LED matrix display handler — routes caught messages to the display engine."""

from __future__ import annotations

import logging

from led_catcher.display import get_display
from led_catcher.models import CaughtMessage
from led_catcher.profile import Profile, match_rule

logger = logging.getLogger(__name__)


def create_led_handler(profile: Profile):
    """Create a LED handler closure with the given profile."""
    display = get_display()

    def led_handler(msg: CaughtMessage) -> None:
        config = match_rule(profile, msg.message)
        if config is None:
            logger.debug("no matching display rule for system=%s severity=%s", msg.message.system, msg.message.severity)
            return

        logger.info(
            "displaying: kind=%s system=%s severity=%s",
            config.kind,
            msg.message.system,
            msg.message.severity,
        )
        display.show(config)

    return led_handler
