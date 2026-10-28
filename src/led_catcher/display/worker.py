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

So: one worker thread owning the panel, and a small handoff queue. The
consumer appends to it and returns immediately. A message that arrives while a
*held* display is up replaces it; one that arrives during a finite display
waits its turn.

How many may wait is ``DISPLAY_QUEUE`` (#142). The default, 1, is a single slot
where the newest message wins: a waiting display is overwritten if a newer one
shows up before the panel is free — a panel is a scoreboard, and the newest
score is the only one worth showing. A larger queue keeps up to that many
finite displays in order, so a burst (three Flux reconciliations in seven
seconds) reaches the panel whole; when it is full the *oldest* waiting one is
dropped, so under pressure the newest still wins.

Held displays stay pre-emptible, queue or not: a pending held display is
superseded by anything submitted after it, so a held display only ever sits at
the end of the queue. Flashing it for zero seconds before whatever follows
would be all it could do, and a score shown after a newer message was the
thing the single slot existed to prevent. A held display submitted behind
waiting finite ones is not moved ahead of them either: it would be replaced by
the first of them at once, and the scoreboard would end up dark. At the back
it is what stays on the panel once the burst has played out.

With an idle screen set (#115), the worker runs it whenever the slot is empty
and no held display is up: after a finite display ends, after blank(), and at
startup. A submission interrupts it at once.

A playlist (#146, display.playlist) takes the idle screen's place while it
runs: the same "nothing else to show" moments, the same interruption by any
submission, and it plays again once the queue is empty. Starting one ends a
held display, as a submission would. blank() stops it.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, replace

logger = logging.getLogger(__name__)

# How often the idle worker rechecks for shutdown. Only reached when nothing is
# on the panel and nothing is queued, so it costs nothing to keep short.
IDLE_POLL_SECONDS = 0.1

# The longest the idle screen goes without checking whether it was switched off
# or changed. A submission does not wait for this: it sets the wake event.
IDLE_SCREEN_POLL_SECONDS = 0.25

# The modes that can hold the panel. The animated ones run for as long as their
# animation takes, whatever `hold` says (docs/profile-reference.md).
HOLDING_KINDS = frozenset({"static", "image", "score", "card"})

# A playlist item shorter than this is not started: what is left of a slot
# that a message took over, drawn for a frame and cleared again.
MIN_PLAYLIST_ITEM_SECONDS = 0.05

# Submitted by blank(): not a display, an instruction to leave the panel dark.
_BLANK = object()


def _is_held(config) -> bool:
    """True for a display that stays up until something replaces it."""
    return bool(getattr(config, "hold", False)) and str(getattr(config, "kind", "")) in HOLDING_KINDS


@dataclass(frozen=True)
class _Entry:
    """A pending display, and what it was for — only for the log line when it is dropped."""

    config: object
    system: str = ""
    title: str = ""


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
        return _is_held(self.config)


class DisplayWorker:
    """Runs displays on a thread of its own, from a queue of ``queue`` pending.

    ``queue=1`` (the default) is a single slot: the newest message wins. Not
    started by the constructor — call :meth:`start`, and :meth:`stop` when
    shutting down.
    """

    def __init__(self, display, idle=None, now=time.time, queue: int = 1) -> None:
        if queue < 1:
            raise ValueError(f"queue must be at least 1, got {queue}")
        self._display = display
        # The idle screen (display.clock.IdleScreen), or None for a dark panel.
        self._idle = idle
        self._idle_active = False
        # The playlist (display.playlist.Playlist), or None (#146).
        self._playlist = None
        self._playlist_active = False
        self._now = now
        self._lock = threading.Lock()
        self._queue_size = queue
        self._pending: deque[_Entry] = deque()
        # Set when a message is submitted or when stopping, so both a held
        # display and an idle worker wake on either.
        self._wake = threading.Event()
        self._stopping = threading.Event()
        # Set by blank() and stop(): the only things allowed to cut a finite
        # display short.
        self._cut = threading.Event()
        self._showing: Showing | None = None
        self._thread: threading.Thread | None = None
        # How many submissions never reached the panel because a newer one
        # replaced them first, or the queue was full (GET /display).
        self._superseded = 0

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="display", daemon=True)
        self._thread.start()
        logger.info("display worker started")

    def submit(self, config, system: str = "", title: str = "") -> None:
        """Hand a display to the worker. Never blocks.

        Called from the consumer's event loop, so it must stay cheap: it takes
        the lock, appends to a short deque and returns. ``system`` and
        ``title`` only name the display in the log if it is dropped.
        """
        with self._lock:
            # A pending held display gives way to anything newer, exactly as
            # one on the panel would — so it is only ever last in the queue.
            if self._pending and _is_held(self._pending[-1].config):
                self._drop(self._pending.pop(), "superseded")
            self._pending.append(_Entry(config, system, title))
            while len(self._pending) > self._queue_size:
                self._drop(self._pending.popleft(), "superseded" if self._queue_size == 1 else "queue full")
            # Set under the lock, paired with the clear in _take, so a message
            # submitted between taking the queue's last entry and clearing the
            # event cannot be lost.
            self._wake.set()

    def blank(self) -> None:
        """Leave the panel dark, now. Never blocks.

        Unlike submit(), this does not wait for a finite display to run out:
        someone asking for a dark panel wants it dark, not dark in eight
        seconds. It still goes through the queue, so a display submitted after
        it wins. Everything waiting is dropped.
        """
        with self._lock:
            self._clear_pending("blanked")
            if self._playlist is not None:
                logger.info("playlist stopped: panel blanked")
                self._playlist = None
            self._pending.append(_Entry(_BLANK))
            self._cut.set()
            self._wake.set()

    @property
    def queue_size(self) -> int:
        """How many displays may wait for the panel (``DISPLAY_QUEUE``)."""
        return self._queue_size

    @property
    def pending(self) -> int:
        """How many displays are waiting for the panel right now."""
        with self._lock:
            return sum(1 for entry in self._pending if entry.config is not _BLANK)

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
    def playlist(self):
        """The playlist, or None. Still reported once run out, until the
        worker notices — readers check ``expired``."""
        with self._lock:
            return self._playlist

    def set_playlist(self, playlist) -> None:
        """Start, replace or (with None) stop the playlist. Never blocks.

        A held display gives way to it, as it would to a submission; a finite
        one keeps its time. A running idle screen or playlist ends at once.
        """
        with self._lock:
            self._playlist = playlist
            if playlist is not None and self._showing is not None and self._showing.held:
                # _showing is cleared here, not by the mode: a held mode
                # returns leaving it set, and the worker only plays a playlist
                # on an empty panel.
                self._showing = None
            # Ends a held display's wait, the idle screen and a running
            # playlist; _take spends it once the queue turns out empty.
            self._wake.set()

    @property
    def playlist_active(self) -> bool:
        """True while a playlist item is what the panel shows."""
        with self._lock:
            return self._playlist_active

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
        with self._lock:
            self._stopping.set()
            self._clear_pending("stopping")
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

    def _drop(self, entry: _Entry, reason: str) -> None:
        """Count and log a display that will never reach the panel. Lock held."""
        if entry.config is _BLANK:
            return
        self._superseded += 1
        logger.info(
            "display dropped before reaching the panel (%s): kind=%s system=%s title=%s",
            reason,
            getattr(entry.config, "kind", "?"),
            entry.system or "-",
            entry.title or "-",
        )

    def _clear_pending(self, reason: str) -> None:
        """Drop everything waiting. Lock held."""
        while self._pending:
            self._drop(self._pending.popleft(), reason)

    def _take(self):
        """Take the next pending display, if any."""
        with self._lock:
            if self._stopping.is_set():
                return None
            if not self._pending:
                # A wake with nothing to take — set_playlist() — is spent here, or the idle screen and the playlist,
                # which both end on it, would end as soon as they start.
                self._wake.clear()
                return None
            config = self._pending.popleft().config
            if not self._pending:
                # Left set while anything is still waiting, so a held display
                # never sits on the panel in front of a queue.
                self._wake.clear()
            # A blank that was superseded has done its job once its entry is
            # taken; left set, it would cut the next display short.
            self._cut.clear()
            return config

    def _run(self) -> None:
        try:
            while not self._stopping.is_set():
                config = self._take()
                if config is None:
                    if self.showing is None and self._playlist_due():
                        self._run_playlist()
                    elif self.idle is not None and self.showing is None:
                        self._run_idle_screen()
                    else:
                        self._wake.wait(timeout=IDLE_POLL_SECONDS)
                    continue
                if self._show(config):
                    self._show_then(config)
        finally:
            self._set_showing(None)
            self._blank()

    def _localise(self, config):
        """The display with an https image swapped for its cached file name.

        A rule can take its picture from the message (#148), and a message
        names a URL, not a file in visual_aid/. POST /display fetches before
        it submits; a message from the stream has nobody to do that but this
        thread, which may block. A picture that cannot be fetched is logged
        and the display skipped -- returning None -- so an overlay still gives
        way to what it played over.
        """
        from led_catcher.display.remote import RemoteImageError, fetch_image, is_remote

        image = str(getattr(config, "image", "") or "")
        if not is_remote(image):
            return config
        try:
            name = fetch_image(image)
        except RemoteImageError as err:
            logger.warning("image not shown, fetching %s failed: %s", image, err)
            return None
        return replace(config, image=name)

    def _show_then(self, config) -> None:
        """After an overlay, what it played over (#148): the same message as
        the next rule draws it, typically the held score after the winner's
        figure. Only on a free panel: anything submitted meanwhile is newer
        than the message the overlay came with, and goes first."""
        then = getattr(config, "then", None)
        if then is None:
            return
        with self._lock:
            if self._pending or self._stopping.is_set():
                return
        self._show(then)

    def _show(self, config) -> bool:
        """Run one display. False when blank() or stop() cut it short."""
        from led_catcher.display.modes import display_event

        if config is _BLANK:
            self._set_showing(None)
            self._blank()
            return False

        config = self._localise(config)
        if config is None:
            return True

        showing = Showing(config, time.time())
        self._set_showing(showing)
        try:
            display_event(self._display, config, wait=self._wait)
        except _Interrupted:
            self._set_showing(None)
            return False
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
        return True

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
            while (
                not self._stopping.is_set() and not self._wake.is_set() and self.idle is idle and self.playlist is None
            ):
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

    def _playlist_due(self) -> bool:
        """True when a playlist is set and has time left. One that ran out is
        dropped here, so the idle screen takes over again."""
        with self._lock:
            playlist = self._playlist
            if playlist is None:
                return False
            if playlist.expired(self._now()):
                logger.info("playlist ended: total of %ss played out", playlist.total)
                self._playlist = None
                return False
            return True

    def _run_playlist(self) -> None:
        """Play the playlist's items until something is submitted, it is
        stopped, replaced or runs out, or the worker stops.

        Each item gets what is left of its slot (display.playlist): after a
        message took part of it, the rest; when a slot is over, the next.
        """
        from led_catcher.display.modes import display_event
        from led_catcher.profile.engine import DisplayConfig

        playlist = self.playlist

        def wait(seconds: float, hold: bool = False, frame: bool = False) -> bool:
            # The playlist's own wait: ends the item at once on a submission,
            # blank(), shutdown, or another playlist. Never a held wait: the
            # gif and image modes are only given finite configs here.
            deadline = time.monotonic() + seconds
            while True:
                if self._stopping.is_set() or self._wake.is_set() or self.playlist is not playlist:
                    raise _Interrupted
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._wake.wait(timeout=min(remaining, IDLE_SCREEN_POLL_SECONDS))

        with self._lock:
            self._playlist_active = True
        try:
            while not self._stopping.is_set() and not self._wake.is_set() and self.playlist is playlist:
                now = self._now()
                slot = playlist.slot(now)
                if slot is None:
                    with self._lock:
                        if self._playlist is playlist:
                            logger.info("playlist ended: total of %ss played out", playlist.total)
                            self._playlist = None
                    break
                left = slot.end - now
                try:
                    if left < MIN_PLAYLIST_ITEM_SECONDS:
                        wait(max(0.0, left))
                        continue
                    config = DisplayConfig(kind="gif", image=slot.image, duration=left)
                    try:
                        display_event(self._display, config, wait=wait)
                    except _Interrupted:
                        raise
                    except Exception:
                        logger.exception("playlist item failed: %s", slot.image)
                        # Sat out rather than retried: the same slot would fail
                        # again at once, as often as the loop can turn.
                        wait(max(0.0, slot.end - self._now()))
                except _Interrupted:
                    break
        finally:
            with self._lock:
                self._playlist_active = False
        if self.playlist is None and self.idle is None and not self._stopping.is_set():
            # Stopped or run out, and no idle screen to take over: dark.
            self._blank()

    def _set_showing(self, showing: Showing | None) -> None:
        with self._lock:
            self._showing = showing

    def _wait(self, seconds: float, hold: bool = False, frame: bool = False) -> bool:
        """Wait out a display.

        A held display waits for a replacement, however long that takes. A
        finite one gets its full time and is not cut short by an arriving
        message — only by shutdown, or by blank(), which unwinds the mode.

        With ``frame``, a held display waits one animation frame of
        ``seconds`` and returns True if a replacement (or blank(), or shutdown)
        arrived meanwhile — so a held card keeps its ticker moving and still
        gives way at once.
        """
        if hold and frame:
            return self._wake.wait(timeout=seconds)
        if hold:
            self._wake.wait()
            return True
        self._cut.wait(timeout=seconds)
        if self._cut.is_set() and not self._stopping.is_set():
            raise _Interrupted
        return False

    def _blank(self) -> None:
        try:
            self._display.clear()
            self._display.swap()
        except Exception:
            logger.exception("failed to clear the panel on shutdown")


_worker: DisplayWorker | None = None


def get_worker(display=None, panel=None, idle=None, queue: int = 1) -> DisplayWorker:
    """The process-wide display worker, started on first use.

    A module-level singleton for the same reason ``get_display`` is one: there
    is one panel, and it can only have one owner. ``panel`` is handed to the
    display when this call is what creates it, and so is ``idle``, the idle
    screen it starts with, and so is ``queue``, the ``DISPLAY_QUEUE`` length.
    """
    global _worker
    if _worker is None:
        from led_catcher.display.matrix import get_display

        _worker = DisplayWorker(display if display is not None else get_display(panel), idle=idle, queue=queue)
        _worker.start()
    return _worker


def reset_worker() -> None:
    """Drop the singleton, stopping it if it is running. For tests and shutdown."""
    global _worker
    if _worker is not None:
        _worker.stop()
        _worker = None
