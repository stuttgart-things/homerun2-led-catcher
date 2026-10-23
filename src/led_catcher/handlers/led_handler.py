"""LED matrix display handler — routes caught messages to the display engine."""

from __future__ import annotations

import logging

from led_catcher.display import get_worker
from led_catcher.models import CaughtMessage
from led_catcher.profile import Profile, match_rule

logger = logging.getLogger(__name__)


def create_led_handler(profile: Profile, panel=None, queue: int = 1):
    """Create a LED handler closure with the given profile.

    The handler hands the display to the worker thread and returns. It used to
    display inline, which blocked the consumer's read loop — and uvicorn with
    it — for the length of every message (#54).

    `panel` is the PanelConfig the matrix is opened with. It comes from the
    already-validated `Config` rather than being read from the environment
    here, so a bad `LED_*` value is reported by the startup guard in
    `__main__` instead of surfacing as a traceback from the first handler.
    `queue` is `DISPLAY_QUEUE`, from the same place.
    """
    worker = get_worker(panel=panel, queue=queue)

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

        # "queued", not "displaying": whether and when it reaches the panel is
        # the worker's call, and it logs a display it drops (#142).
        logger.info(
            "queued for display: kind=%s system=%s severity=%s hold=%s",
            config.kind,
            msg.message.system,
            msg.message.severity,
            config.hold,
        )
        worker.submit(config, system=msg.message.system, title=msg.message.title)

    return led_handler
