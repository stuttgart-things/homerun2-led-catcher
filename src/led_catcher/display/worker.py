"""The thread that owns the matrix.

Displaying is slow by design: a message stays on the panel for its duration,
and a held one stays until something replaces it. That work cannot happen on
the consumer's event loop — the display handler used to be called inline from
``RedisConsumer._process_entry``, so a display of ``duration`` seconds stopped
the catcher reading the stream, and stopped uvicorn answering ``/healthz``, for
exactly that long (issue #54).

It also cannot happen on a thread per message: two messages arriving close
together would give two threads interleaving ``clear`` / ``draw_text`` /
``swap`` on the same canvas, which is a worse failure than the one being fixed.

So: one worker thread owning the panel, and a single-slot handoff where the
newest message wins. The consumer writes the slot and returns immediately. A
message that arrives while a *held* display is up replaces it; one that arrives
during a finite display waits its turn, and is itself overwritten if a newer
one shows up before the panel is free. That is the intended trade — a panel is
a scoreboard, not a queue, and the newest score is the only one worth showing.

With an idle screen set (#115), the worker runs it whenever the slot is empty
and no held display is up: after a finite display ends, after blank(), and at
startup. A submission interrupts it at once.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# How often the idle worker rechecks for shutdown. Only reached when nothing is
# on the panel and nothing is queued, so it costs nothing to keep short.
IDLE_POLL_SECONDS = 0.1

# The longest the idle screen goes without checking whether it was switched off
# or changed. A submission does not wait for this: it sets the wake event.
IDLE_SCREEN_POLL_SECONDS = 0.25

# The modes that can hold the panel. The animated ones run for as long as their
# animation takes, whatever `hold` says (docs/profile-reference.md).
HOLDING_KINDS = frozenset({"static", "image", "score"})

# Submitted by blank(): not a display, an instruction to leave the panel dark.
_BLANK = object()


class _Interrupted(Exception):
    """Raised out of a display's wait when blank() cuts it short."""


@dataclass(frozen=True)
class Showing:
    """What the panel is showing, and since when (wall clock)."""

    config: object
    since: float

    @property
    def held(self) -> bool:
        """True when the display stays up until something replaces it."""
        return bool(getattr(self.config, "hold", False)) and str(getattr(self.config, "kind", "")) in HOLDING_KINDS


class DisplayWorker:
    """Runs displays on a thread of its own, newest message wins.

    Not started by the constructor — call :meth:`start`, and :meth:`stop` when
    shutting down.
    """

    def __init__(self, display, idle=None, now=time.time) -> None:
        self._display = display
        # The idle screen (display.clock.IdleScreen), or None for a dark panel.
        self._idle = idle
        self._idle_active = False
        self._now = now
        self._lock = threading.Lock()
        self._pending = None
        # Set when a message is submitted or when stopping, so both a held
        # display and an idle worker wake on either.
        self._wake = threading.Event()
        self._stopping = threading.Event()
        # Set by blank() and stop(): the only things allowed to cut a finite
        # display short.
        self._cut = threading.Event()
        self._showing: Showing | None = None
        self._thread: threading.Thread | None = None
        # Purely for tests and logging: how many submissions never reached the
        # panel because a newer one replaced them first.
        self._superseded = 0

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="display", daemon=True)
        self._thread.start()
        logger.info("display worker started")

    def submit(self, config) -> None:
        """Hand a display to the worker. Never blocks.

        Called from the consumer's event loop, so it must stay cheap: it takes
        the lock, writes one reference and returns.
        """
        with self._lock:
            if self._pending is not None:
                self._superseded += 1
            self._pending = config
            # Set under the lock, paired with the clear in _take, so a message
            # submitted between taking the slot and clearing the event cannot
            # be lost.
            self._wake.set()

    def blank(self) -> None:
        """Leave the panel dark, now. Never blocks.

        Unlike submit(), this does not wait for a finite display to run out:
        someone asking for a dark panel wants it dark, not dark in eight
        seconds. It still goes through the slot, so a display submitted after
        it wins.
        """
        with self._lock:
            if self._pending is not None:
                self._superseded += 1
            self._pending = _BLANK
            self._cut.set()
            self._wake.set()

    @property
    def showing(self) -> Showing | None:
        """What is on the panel right now, or None when it is dark."""
        with self._lock:
            return self._showing

    @property
    def idle(self):
        """The idle screen, or None when the panel goes dark between displays."""
        with self._lock:
            return self._idle

    def set_idle(self, idle) -> None:
        """Switch the idle screen. Never blocks; a running one notices within
        IDLE_SCREEN_POLL_SECONDS."""
        with self._lock:
            self._idle = idle

    @property
    def idle_active(self) -> bool:
        """True while the idle screen is what the panel shows."""
        with self._lock:
            return self._idle_active

    @property
    def alive(self) -> bool:
        """Whether the worker thread is running. False before start() and after stop()."""
        thread = self._thread
        return thread is not None and thread.is_alive()

    def stop(self, timeout: float = 5.0) -> None:
        """Stop the worker and leave the panel dark."""
        self._stopping.set()
        self._cut.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                logger.warning("display worker did not stop within %ss", timeout)
            self._thread = None
        if self._superseded:
            logger.info("%d display(s) superseded before reaching the panel", self._superseded)
        logger.info("display worker stopped")

    @property
    def superseded(self) -> int:
        with self._lock:
            return self._superseded

    def _take(self):
        """Take the pending display, if any."""
        with self._lock:
            config, self._pending = self._pending, None
            if config is not None:
                self._wake.clear()
                # A blank that was superseded has done its job once the slot
                # is taken; left set, it would cut the next display short.
                if not self._stopping.is_set():
                    self._cut.clear()
            return config

    def _run(self) -> None:
        try:
            while not self._stopping.is_set():
                config = self._take()
                if config is None:
                    if self.idle is not None and self.showing is None:
                        self._run_idle_screen()
                    else:
                        self._wake.wait(timeout=IDLE_POLL_SECONDS)
                    continue
                self._show(config)
        finally:
            self._set_showing(None)
            self._blank()

    def _show(self, config) -> None:
        from led_catcher.display.modes import display_event

        if config is _BLANK:
            self._set_showing(None)
            self._blank()
            return

        showing = Showing(config, time.time())
        self._set_showing(showing)
        try:
            display_event(self._display, config, wait=self._wait)
        except _Interrupted:
            self._set_showing(None)
            return
        except Exception:
            # A broken font or image must not take the worker down with it, or
            # the panel stays on whatever it happened to be showing.
            logger.exception("display failed: kind=%s", getattr(config, "kind", "?"))
        # A held display is left lit when its mode returns; anything else has
        # cleared the panel by now.
        if not showing.held:
            with self._lock:
                if self._showing is showing:
                    self._showing = None

    def _run_idle_screen(self) -> None:
        """Show the idle screen until something is submitted, it is switched or
        changed, or the worker stops.

        Redrawn only when the face changes, once a second for the blinking
        colon. A submission sets the wake event, so it is never kept waiting;
        the next display clears the panel before it draws.
        """
        from led_catcher.display.clock import draw_clock

        idle = self.idle
        drawn_second = None
        with self._lock:
            self._idle_active = True
        try:
            while not self._stopping.is_set() and not self._wake.is_set() and self.idle is idle:
                now = self._now()
                if int(now) != drawn_second:
                    try:
                        draw_clock(self._display, idle, now)
                    except Exception:
                        # Switched off rather than retried: the next round
                        # would fail the same way, once per poll, forever.
                        logger.exception("idle screen failed, switching it off")
                        self.set_idle(None)
                        self._blank()
                        return
                    drawn_second = int(now)
                until_next_second = 1.0 - (now % 1.0)
                self._wake.wait(timeout=min(until_next_second, IDLE_SCREEN_POLL_SECONDS))
        finally:
            with self._lock:
                self._idle_active = False
        if self.idle is None and not self._stopping.is_set():
            # Switched off: dark, as without an idle screen.
            self._blank()

    def _set_showing(self, showing: Showing | None) -> None:
        with self._lock:
            self._showing = showing

    def _wait(self, seconds: float, hold: bool = False) -> None:
        """Wait out a display.

        A held display waits for a replacement, however long that takes. A
        finite one gets its full time and is not cut short by an arriving
        message — only by shutdown, or by blank(), which unwinds the mode.
        """
        if hold:
            self._wake.wait()
            return
        self._cut.wait(timeout=seconds)
        if self._cut.is_set() and not self._stopping.is_set():
            raise _Interrupted

    def _blank(self) -> None:
        try:
            self._display.clear()
            self._display.swap()
        except Exception:
            logger.exception("failed to clear the panel on shutdown")


_worker: DisplayWorker | None = None


def get_worker(display=None, panel=None, idle=None) -> DisplayWorker:
    """The process-wide display worker, started on first use.

    A module-level singleton for the same reason ``get_display`` is one: there
    is one panel, and it can only have one owner. ``panel`` is handed to the
    display when this call is what creates it, and so is ``idle``, the idle
    screen it starts with.
    """
    global _worker
    if _worker is None:
        from led_catcher.display.matrix import get_display

        _worker = DisplayWorker(display if display is not None else get_display(panel), idle=idle)
        _worker.start()
    return _worker


def reset_worker() -> None:
    """Drop the singleton, stopping it if it is running. For tests and shutdown."""
    global _worker
    if _worker is not None:
        _worker.stop()
        _worker = None
