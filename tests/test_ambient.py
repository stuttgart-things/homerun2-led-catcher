"""Ambient displays: programme between notifications, never in their way (#161)."""

from __future__ import annotations

import time

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from led_catcher.config.settings import ApiConfig
from led_catcher.display.clock import IdleScreen
from led_catcher.display.worker import DisplayWorker
from led_catcher.handlers.display_api import create_display_router
from led_catcher.profile import DisplayConfig, load_profile
from led_catcher.web import EventTracker

from .test_display_worker import TICK, FakeDisplay, wait_for

LONG = 2.0
TOKEN = "s3cret"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def static(text: str, duration: float = TICK, hold: bool = False, ambient: bool = False) -> DisplayConfig:
    return DisplayConfig(kind="static", text=text, duration=duration, hold=hold, ambient=ambient)


@pytest.fixture
def worker():
    display = FakeDisplay()
    w = DisplayWorker(display, queue=3)
    w.start()
    yield w, display
    w.stop(timeout=2)


def test_ambient_on_a_free_panel_is_shown(worker):
    w, display = worker

    w.submit(static("NEWS", ambient=True))

    assert wait_for(lambda: "NEWS" in display.snapshot())
    assert w.superseded == 0


def test_ambient_during_a_notification_is_dropped_not_queued(worker):
    w, display = worker
    w.submit(static("ALERT", duration=0.3))
    assert wait_for(lambda: "ALERT" in display.snapshot())

    w.submit(static("NEWS", ambient=True))
    time.sleep(0.5)

    assert "NEWS" not in display.snapshot()
    assert w.superseded == 1


def test_ambient_behind_a_waiting_notification_is_dropped(worker):
    w, display = worker
    w.submit(static("ALERT 1", duration=0.2))
    w.submit(static("ALERT 2", duration=TICK))

    w.submit(static("NEWS", ambient=True))
    time.sleep(0.5)

    assert display.snapshot() == ["ALERT 1", "ALERT 2"]


def test_ambient_during_a_held_score_is_dropped_and_the_score_stays(worker):
    w, display = worker
    w.submit(static("3:2", hold=True))
    assert wait_for(lambda: w.showing is not None and w.showing.held)

    w.submit(static("NEWS", ambient=True))
    time.sleep(0.2)

    assert "NEWS" not in display.snapshot()
    assert w.showing.held and w.showing.config.text == "3:2"


def test_a_notification_cuts_a_running_ambient_display_short(worker):
    w, display = worker
    w.submit(static("NEWS", duration=LONG, ambient=True))
    assert wait_for(lambda: w.showing is not None and w.showing.ambient)

    started = time.monotonic()
    w.submit(static("ALERT"))

    assert wait_for(lambda: "ALERT" in display.snapshot(), timeout=0.5)
    assert time.monotonic() - started < 0.5, "the alert waited for the programme"


def test_a_notification_removes_waiting_ambient_displays(worker):
    w, display = worker
    w.submit(static("NEWS 1", duration=0.2, ambient=True))
    assert wait_for(lambda: "NEWS 1" in display.snapshot())
    w.submit(static("NEWS 2", ambient=True))
    w.submit(static("ALERT"))

    assert wait_for(lambda: "ALERT" in display.snapshot())
    time.sleep(0.2)
    assert "NEWS 2" not in display.snapshot()


def test_the_newest_ambient_display_replaces_a_running_one(worker):
    w, display = worker
    w.submit(static("NEWS 1", duration=LONG, ambient=True))
    assert wait_for(lambda: "NEWS 1" in display.snapshot())

    started = time.monotonic()
    w.submit(static("NEWS 2", ambient=True))

    assert wait_for(lambda: "NEWS 2" in display.snapshot(), timeout=0.5)
    assert time.monotonic() - started < 0.5


class ClockDisplay(FakeDisplay):
    def set_pixel(self, x, y, r, g, b) -> None:
        pass


def test_ambient_interrupts_the_idle_screen_which_comes_back():
    display = ClockDisplay()
    w = DisplayWorker(display, idle=IdleScreen(color=(0, 0, 255)), queue=3)
    w.start()
    try:
        assert wait_for(lambda: w.idle_active)
        w.submit(static("NEWS", ambient=True))
        assert wait_for(lambda: "NEWS" in display.snapshot())
        assert wait_for(lambda: w.idle_active), "the clock returns after the programme item"
    finally:
        w.stop(timeout=2)


def test_an_ambient_display_is_never_held(worker):
    w, display = worker

    w.submit(static("NEWS", hold=True, ambient=True))

    assert wait_for(lambda: "NEWS" in display.snapshot())
    assert wait_for(lambda: w.showing is None), "an ambient display does not sit on the panel"


# ---- the profile ----------------------------------------------------------------


def test_ambient_is_read_from_the_profile_and_never_held(tmp_path):
    path = tmp_path / "profile.yaml"
    path.write_text(
        "displayRules:\n"
        "  radio:\n"
        "    systems: [radio]\n"
        "    severity: [INFO]\n"
        "    kind: card\n"
        "    ambient: true\n"
        "    hold: true\n"
        "  notification:\n"
        "    systems: ['*']\n"
        "    severity: [INFO]\n"
        "    kind: text\n"
    )

    profile = load_profile(path)

    assert profile.rules["radio"].ambient is True and profile.rules["radio"].hold is False
    assert profile.rules["notification"].ambient is False


def test_ambient_survives_rule_matching_into_the_simulators_event(tmp_path):
    from led_catcher.models import Message
    from led_catcher.profile import match_rule
    from led_catcher.web.app import _event_json
    from led_catcher.web.handler import build_event

    path = tmp_path / "profile.yaml"
    path.write_text(
        "displayRules:\n  radio:\n    systems: [radio]\n    severity: [INFO]\n    kind: static\n    ambient: true\n"
    )
    profile = load_profile(path)
    msg = Message(title="214 pods on platform", message="", severity="INFO", system="radio")

    config = match_rule(profile, msg)

    assert config.ambient is True
    assert _event_json(build_event(profile, msg, config))["ambient"] is True


# ---- POST /display --------------------------------------------------------------


class FakeWorker:
    def __init__(self) -> None:
        self.submitted = []
        self.showing = None
        self.idle_active = False
        self.playlist = None
        self.playlist_active = False
        self.queue_size = 1
        self.pending = 0
        self.superseded = 0

    def submit(self, config, system: str = "", title: str = "") -> None:
        self.submitted.append(config)


async def test_post_display_takes_ambient_and_drops_hold_with_it():
    worker = FakeWorker()
    app = FastAPI()
    app.include_router(create_display_router(worker, ApiConfig(token=TOKEN), tracker=EventTracker()))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        res = await c.post(
            "/display", headers=AUTH, json={"kind": "static", "text": "NEWS", "ambient": True, "hold": True}
        )

    assert res.status_code == 200, res.text
    assert res.json()["display"]["ambient"] is True
    assert worker.submitted[0].ambient is True and worker.submitted[0].hold is False
