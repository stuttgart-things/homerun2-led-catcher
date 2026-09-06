"""LED matrix display handler — routes caught messages to the display engine."""

from __future__ import annotations

import logging

from led_catcher.display import get_worker
from led_catcher.models import CaughtMessage
from led_catcher.profile import Profile, match_rule

logger = logging.getLogger(__name__)


def create_led_handler(profile: Profile):
    """Create a LED handler closure with the given profile.

    The handler hands the display to the worker thread and returns. It used to
    display inline, which blocked the consumer's read loop — and uvicorn with
    it — for the length of every message (#54).
    """
    worker = get_worker()

    def led_handler(msg: CaughtMessage) -> None:
        config = match_rule(profile, msg.message)
        if config is None:
            # Deliberately not debug: at the default LOG_LEVEL=info a dropped
            # message left no trace at all, so a missing rule for a severity
            # that matters (ERROR/CRITICAL had none until 2026-09-05) was
            # indistinguishable from a quiet bus.
            logger.info(
                "no matching display rule, message NOT displayed: system=%s severity=%s",
                msg.message.system,
                msg.message.severity,
            )
            return

        logger.info(
            "displaying: kind=%s system=%s severity=%s hold=%s",
            config.kind,
            msg.message.system,
            msg.message.severity,
            config.hold,
        )
        worker.submit(config)

    return led_handler
