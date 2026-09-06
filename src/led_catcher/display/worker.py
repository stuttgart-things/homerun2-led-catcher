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
"""

from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)

# How often the idle worker rechecks for shutdown. Only reached when nothing is
# on the panel and nothing is queued, so it costs nothing to keep short.
IDLE_POLL_SECONDS = 0.1


class DisplayWorker:
    """Runs displays on a thread of its own, newest message wins.

    Not started by the constructor — call :meth:`start`, and :meth:`stop` when
    shutting down.
    """

    def __init__(self, display) -> None:
        self._display = display
        self._lock = threading.Lock()
        self._pending = None
        # Set when a message is submitted or when stopping, so both a held
        # display and an idle worker wake on either.
        self._wake = threading.Event()
        self._stopping = threading.Event()
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

    def stop(self, timeout: float = 5.0) -> None:
        """Stop the worker and leave the panel dark."""
        self._stopping.set()
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
            return config

    def _run(self) -> None:
        try:
            while not self._stopping.is_set():
                config = self._take()
                if config is None:
                    self._wake.wait(timeout=IDLE_POLL_SECONDS)
                    continue
                self._show(config)
        finally:
            self._blank()

    def _show(self, config) -> None:
        from led_catcher.display.modes import display_event

        try:
            display_event(self._display, config, wait=self._wait)
        except Exception:
            # A broken font or image must not take the worker down with it, or
            # the panel stays on whatever it happened to be showing.
            logger.exception("display failed: kind=%s", getattr(config, "kind", "?"))

    def _wait(self, seconds: float, hold: bool = False) -> None:
        """Wait out a display.

        A held display waits for a replacement, however long that takes. A
        finite one gets its full time and is not cut short by an arriving
        message — only by shutdown.
        """
        if hold:
            self._wake.wait()
            return
        self._stopping.wait(timeout=seconds)

    def _blank(self) -> None:
        try:
            self._display.clear()
            self._display.swap()
        except Exception:
            logger.exception("failed to clear the panel on shutdown")


_worker: DisplayWorker | None = None


def get_worker(display=None) -> DisplayWorker:
    """The process-wide display worker, started on first use.

    A module-level singleton for the same reason ``get_display`` is one: there
    is one panel, and it can only have one owner.
    """
    global _worker
    if _worker is None:
        from led_catcher.display.matrix import get_display

        _worker = DisplayWorker(display if display is not None else get_display())
        _worker.start()
    return _worker


def reset_worker() -> None:
    """Drop the singleton, stopping it if it is running. For tests and shutdown."""
    global _worker
    if _worker is not None:
        _worker.stop()
        _worker = None
