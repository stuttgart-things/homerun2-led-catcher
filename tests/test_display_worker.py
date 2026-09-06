"""Tests for the display worker (issue #54).

The properties that matter here are about time and threads: that submitting is
cheap, that a held display gives way to a newer one, that a finite one does
not, and that only ever one thing draws on the panel.
"""

from __future__ import annotations

import logging
import threading
import time

import pytest

from led_catcher.display.worker import DisplayWorker
from led_catcher.profile import DisplayConfig

# Long enough to be unambiguous against scheduling jitter, short enough that the
# suite stays fast.
TICK = 0.05


class FakeDisplay:
    """A panel that records what was drawn on it and notices interleaving."""

    def __init__(self, draw_time: float = 0.0) -> None:
        self._lock = threading.Lock()
        self._drawing = False
        self._draw_time = draw_time
        self.overlaps = 0
        self.texts: list[str] = []
        self.clears = 0
        self.swaps = 0
        self.drawn = threading.Event()

    def _enter(self) -> None:
        with self._lock:
            if self._drawing:
                self.overlaps += 1
            self._drawing = True

    def _exit(self) -> None:
        with self._lock:
            self._drawing = False

    def draw_text(self, font_path, x, y, color, text) -> int:
        self._enter()
        try:
            if self._draw_time:
                time.sleep(self._draw_time)
            with self._lock:
                self.texts.append(text)
            self.drawn.set()
            return len(text) * 6
        finally:
            self._exit()

    def show_image(self, image) -> None:
        self._enter()
        self._exit()

    def clear(self) -> None:
        with self._lock:
            self.clears += 1

    def swap(self) -> None:
        with self._lock:
            self.swaps += 1

    def snapshot(self) -> list[str]:
        with self._lock:
            return list(self.texts)


@pytest.fixture
def worker():
    display = FakeDisplay()
    w = DisplayWorker(display)
    w.start()
    yield w, display
    w.stop(timeout=2)


def static(text: str, duration: float = TICK, hold: bool = False) -> DisplayConfig:
    return DisplayConfig(kind="static", text=text, duration=duration, hold=hold)


def wait_for(predicate, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False


def test_a_submitted_display_reaches_the_panel(worker):
    w, display = worker
    w.submit(static("hello"))

    assert wait_for(lambda: display.snapshot() == ["hello"])


# The bug in #54: displaying happened inline, so the caller paid for the whole
# duration. A submit must cost nothing regardless of what is on the panel.
def test_submit_does_not_wait_for_the_panel(worker):
    w, display = worker
    w.submit(static("first", duration=5.0))
    assert wait_for(lambda: display.snapshot() == ["first"])

    start = time.monotonic()
    for i in range(50):
        w.submit(static(f"m{i}"))
    elapsed = time.monotonic() - start

    assert elapsed < 0.5, f"50 submits took {elapsed:.2f}s behind a 5s display"


def test_a_held_display_is_replaced_by_the_next_message(worker):
    w, display = worker
    # duration is irrelevant to a held display; make it absurd so a test that
    # accidentally waits it out fails loudly rather than passing slowly.
    w.submit(static("7:5", duration=3600, hold=True))
    assert wait_for(lambda: display.snapshot() == ["7:5"])

    w.submit(static("8:5", duration=3600, hold=True))
    assert wait_for(lambda: display.snapshot() == ["7:5", "8:5"]), "a held display did not give way to the next message"


def test_a_held_display_stays_up_with_nothing_to_replace_it(worker):
    w, display = worker
    w.submit(static("7:5", duration=TICK, hold=True))
    assert wait_for(lambda: display.snapshot() == ["7:5"])

    clears_after_draw = display.clears
    time.sleep(TICK * 4)

    # Still exactly one draw, and the panel was not blanked when the (ignored)
    # duration elapsed.
    assert display.snapshot() == ["7:5"]
    assert display.clears == clears_after_draw, "a held display was cleared"


# The other half of the decision: a finite duration is a promise of screen time.
def test_a_finite_display_is_not_cut_short_by_a_new_message(worker):
    w, display = worker
    w.submit(static("first", duration=TICK * 6))
    assert wait_for(lambda: display.snapshot() == ["first"])

    start = time.monotonic()
    w.submit(static("second"))
    assert wait_for(lambda: display.snapshot() == ["first", "second"])
    elapsed = time.monotonic() - start

    assert elapsed >= TICK * 4, f"the second message displaced the first after only {elapsed:.3f}s"


def test_only_the_newest_of_several_queued_messages_is_shown(worker):
    w, display = worker
    w.submit(static("on-screen", duration=TICK * 4))
    assert wait_for(lambda: display.snapshot() == ["on-screen"])

    # Three arrive while the panel is busy. A scoreboard wants the last one,
    # not a replay of all three.
    for text in ("stale-1", "stale-2", "newest"):
        w.submit(static(text))

    assert wait_for(lambda: display.snapshot() == ["on-screen", "newest"])
    time.sleep(TICK * 2)
    assert display.snapshot() == ["on-screen", "newest"]
    assert w.superseded == 2


# One worker owning the panel is the reason a thread per message was rejected.
def test_nothing_ever_draws_on_the_panel_twice_at_once():
    display = FakeDisplay(draw_time=0.01)
    w = DisplayWorker(display)
    w.start()
    try:
        stop = threading.Event()

        def flood():
            while not stop.is_set():
                w.submit(static("x", duration=0.001))

        threads = [threading.Thread(target=flood) for _ in range(4)]
        for t in threads:
            t.start()
        time.sleep(0.5)
        stop.set()
        for t in threads:
            t.join()

        assert display.overlaps == 0, f"{display.overlaps} interleaved draws"
        assert display.snapshot(), "nothing was drawn at all"
    finally:
        w.stop(timeout=2)


def test_a_failing_display_does_not_kill_the_worker(worker, caplog):
    w, display = worker

    class Boom:
        kind = "static"
        text = "bad"
        duration = TICK
        hold = False
        color = (255, 255, 255)

        @property
        def font(self):
            raise RuntimeError("no font for you")

    w.submit(Boom())
    assert wait_for(lambda: "display failed" in caplog.text)

    # And the next ordinary message still gets through.
    w.submit(static("recovered"))
    assert wait_for(lambda: "recovered" in display.snapshot())


def test_stop_leaves_the_panel_dark():
    display = FakeDisplay()
    w = DisplayWorker(display)
    w.start()
    w.submit(static("7:5", duration=3600, hold=True))
    assert wait_for(lambda: display.snapshot() == ["7:5"])

    clears_before = display.clears
    w.stop(timeout=2)

    assert display.clears > clears_before, "the panel was left lit after shutdown"


def test_stop_interrupts_a_held_display_promptly():
    display = FakeDisplay()
    w = DisplayWorker(display)
    w.start()
    w.submit(static("held", duration=3600, hold=True))
    assert wait_for(lambda: display.snapshot() == ["held"])

    start = time.monotonic()
    w.stop(timeout=5)
    assert time.monotonic() - start < 1.0, "shutdown waited on a held display"


def test_stop_interrupts_a_long_finite_display_promptly():
    display = FakeDisplay()
    w = DisplayWorker(display)
    w.start()
    w.submit(static("long", duration=3600))
    assert wait_for(lambda: display.snapshot() == ["long"])

    start = time.monotonic()
    w.stop(timeout=5)
    assert time.monotonic() - start < 1.0, "shutdown waited out the duration"


def test_stop_is_safe_before_anything_was_submitted():
    w = DisplayWorker(FakeDisplay())
    w.start()
    w.stop(timeout=2)


def test_stop_is_safe_on_a_worker_that_never_started():
    DisplayWorker(FakeDisplay()).stop(timeout=2)


# --- the handler, which is where #54 actually bit -------------------------


def _caught(system: str = "demo", severity: str = "info", title: str = "hi"):
    from led_catcher.models import CaughtMessage, Message

    return CaughtMessage(message=Message(system=system, severity=severity, title=title))


def test_the_led_handler_returns_before_the_display_is_over(monkeypatch):
    """The regression #54 is about.

    The handler is called synchronously from the consumer's read loop, so its
    cost is the cost of not reading the stream. It used to be the full display
    duration.
    """
    import led_catcher.display.worker as worker_module
    from led_catcher.handlers.led_handler import create_led_handler
    from led_catcher.profile import DisplayConfig, Profile

    display = FakeDisplay()
    monkeypatch.setattr(worker_module, "_worker", None)
    monkeypatch.setattr("led_catcher.display.matrix.get_display", lambda: display)

    profile = Profile(
        rules={
            "slow": DisplayConfig(kind="static", text="{{ title }}", duration=3600, systems=["*"], severity=["info"])
        }
    )
    handler = create_led_handler(profile)
    try:
        start = time.monotonic()
        handler(_caught(title="7:5"))
        elapsed = time.monotonic() - start

        assert elapsed < 0.5, f"the handler blocked for {elapsed:.2f}s on a 3600s display"
        assert wait_for(lambda: display.snapshot() == ["7:5"])

        # And a second message is accepted just as quickly while the first is up.
        start = time.monotonic()
        handler(_caught(title="8:5"))
        assert time.monotonic() - start < 0.5
    finally:
        worker_module.reset_worker()


def test_the_led_handler_still_drops_a_message_with_no_rule(monkeypatch, caplog):
    import led_catcher.display.worker as worker_module
    from led_catcher.handlers.led_handler import create_led_handler
    from led_catcher.profile import Profile

    display = FakeDisplay()
    monkeypatch.setattr(worker_module, "_worker", None)
    monkeypatch.setattr("led_catcher.display.matrix.get_display", lambda: display)

    caplog.set_level(logging.INFO)
    handler = create_led_handler(Profile(rules={}))
    try:
        handler(_caught())
        assert "no matching display rule" in caplog.text
        time.sleep(TICK)
        assert display.snapshot() == []
    finally:
        worker_module.reset_worker()


# --- hold in the profile --------------------------------------------------


def test_hold_is_read_from_the_profile(tmp_path):
    from led_catcher.profile import load_profile

    path = tmp_path / "profile.yaml"
    path.write_text(
        "displayRules:\n"
        "  score:\n"
        "    systems: [tabletennis]\n"
        "    severity: [INFO]\n"
        "    kind: static\n"
        "    text: '{{ title }}'\n"
        "    hold: true\n"
        "  notification:\n"
        "    systems: ['*']\n"
        "    severity: [INFO]\n"
        "    kind: text\n"
        "    text: '{{ title }}'\n"
        "    duration: 5\n"
    )

    profile = load_profile(path)
    assert profile.rules["score"].hold is True
    assert profile.rules["notification"].hold is False


def test_hold_survives_rule_matching(tmp_path):
    """The resolved config is a copy, so a field not copied is silently lost."""
    from led_catcher.models import Message
    from led_catcher.profile import load_profile, match_rule

    path = tmp_path / "profile.yaml"
    path.write_text(
        "displayRules:\n"
        "  score:\n"
        "    systems: [tabletennis]\n"
        "    severity: [INFO]\n"
        "    kind: static\n"
        "    text: '{{ title }}'\n"
        "    hold: true\n"
    )

    resolved = match_rule(load_profile(path), Message(system="tabletennis", severity="info", title="7:5"))
    assert resolved is not None
    assert resolved.hold is True
    assert resolved.text == "7:5"


def test_a_direct_display_call_does_not_block_on_hold():
    """display_event without a worker has nothing to wait for.

    Blocking forever would be the wrong answer for a one-shot render — the
    honest one is to leave the panel lit and return.
    """
    from led_catcher.display.modes import display_event

    display = FakeDisplay()
    start = time.monotonic()
    display_event(display, static("7:5", duration=3600, hold=True))

    assert time.monotonic() - start < 0.5
    assert display.snapshot() == ["7:5"]
