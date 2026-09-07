"""Tests for the HTMX web simulator."""

import pytest
from httpx import ASGITransport, AsyncClient

from led_catcher.models import CaughtMessage, Message
from led_catcher.profile import DisplayConfig, Profile
from led_catcher.web import EventTracker, LedEvent, create_web_app, create_web_handler


def test_event_tracker_record():
    tracker = EventTracker(max_events=5)
    assert tracker.total == 0

    event = LedEvent(timestamp="12:00:00", severity="error", system="github", title="Build failed")
    tracker.record(event)
    assert tracker.total == 1
    assert tracker.recent(1)[0].title == "Build failed"


def test_event_tracker_ring_buffer():
    tracker = EventTracker(max_events=3)
    for i in range(5):
        tracker.record(LedEvent(timestamp=f"12:0{i}:00", title=f"Event {i}"))
    assert tracker.total == 3
    assert tracker.recent(10)[0].title == "Event 4"


def test_event_tracker_version():
    tracker = EventTracker()
    v0 = tracker.version
    tracker.record(LedEvent())
    assert tracker.version == v0 + 1


def test_led_event_severity_css():
    assert LedEvent(severity="error").severity_css() == "severity-error"
    assert LedEvent(severity="WARNING").severity_css() == "severity-warning"
    assert LedEvent(severity="success").severity_css() == "severity-success"
    assert LedEvent(severity="info").severity_css() == "severity-info"
    assert LedEvent(severity="unknown").severity_css() == "severity-info"


def test_led_event_color_hex():
    assert LedEvent(color=(255, 0, 0)).color_hex() == "#ff0000"
    assert LedEvent(color=(0, 100, 255)).color_hex() == "#0064ff"


def test_web_handler_records_event():
    profile = Profile()
    tracker = EventTracker()
    handler = create_web_handler(profile, tracker)

    msg = Message(title="Deploy OK", severity="info", system="flux", author="ci")
    caught = CaughtMessage(message=msg, object_id="e:1", stream_id="1-0")
    handler(caught)

    assert tracker.total == 1
    event = tracker.recent(1)[0]
    assert event.title == "Deploy OK"
    assert event.system == "flux"
    assert event.severity == "info"


@pytest.mark.asyncio
async def test_web_app_index():
    tracker = EventTracker()
    app = create_web_app(tracker, version="1.0.0", commit="abc1234", date="2024-01-15")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/")
    assert resp.status_code == 200
    assert "HOMERUN" in resp.text
    assert "1.0.0" in resp.text


@pytest.mark.asyncio
async def test_web_app_events_partial():
    tracker = EventTracker()
    tracker.record(LedEvent(timestamp="12:00:00", severity="error", system="github", title="Failed"))
    app = create_web_app(tracker)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/events")
    assert resp.status_code == 200
    assert "github" in resp.text
    assert "Failed" in resp.text


@pytest.mark.asyncio
async def test_web_app_api_events():
    tracker = EventTracker()
    tracker.record(LedEvent(timestamp="12:00:00", severity="info", system="flux", title="OK", color=(0, 255, 0)))
    app = create_web_app(tracker)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/events")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["system"] == "flux"
    assert data[0]["color"] == "#00ff00"


@pytest.mark.asyncio
async def test_web_app_api_events_carries_the_payload():
    """The scoreboard canvas draws from `message`; without it a score event
    reaches the simulator as a coloured row with nothing to render."""
    tracker = EventTracker()
    tracker.record(
        LedEvent(
            timestamp="12:00:00",
            severity="info",
            system="tabletennis",
            title="8:6",
            kind="score",
            message="points=8:6;serve=b",
        )
    )
    app = create_web_app(tracker)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/events")
    data = resp.json()
    assert data[0]["kind"] == "score"
    assert data[0]["message"] == "points=8:6;serve=b"


@pytest.mark.asyncio
async def test_web_app_empty_events():
    tracker = EventTracker()
    app = create_web_app(tracker)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/events")
    assert resp.status_code == 200
    assert "No events yet" in resp.text


# --- the simulator has to show what the matrix shows (#62) -----------------
#
# The canvas used to hold every event for a hardcoded five seconds and then
# draw the clock, whatever the profile said. A score with `hold: true` was
# therefore replaced by a clock while the hardware kept it up — and the
# simulator is precisely the tool for checking a profile without hardware.


def _profile_with(rule: dict) -> Profile:
    return Profile(rules={"r": DisplayConfig(**rule)})


def test_the_recorded_event_carries_what_the_rule_decided():
    tracker = EventTracker()
    profile = _profile_with(
        dict(kind="static", text="{{ title }}", hold=True, duration=3, systems=["*"], severity=["info"])
    )
    handler = create_web_handler(profile, tracker)

    handler(CaughtMessage(message=Message(title="7:5", severity="info", system="tabletennis")))

    event = tracker.recent(1)[0]
    assert event.hold is True
    assert event.duration == 3


def test_an_unheld_rule_is_recorded_as_such():
    tracker = EventTracker()
    profile = _profile_with(dict(kind="text", text="{{ title }}", duration=7, systems=["*"], severity=["info"]))
    handler = create_web_handler(profile, tracker)

    handler(CaughtMessage(message=Message(title="build ok", severity="info", system="github")))

    event = tracker.recent(1)[0]
    assert event.hold is False
    assert event.duration == 7


def test_a_message_matching_no_rule_still_gets_a_duration():
    """It is recorded uncoloured by rule; the canvas still needs a number."""
    tracker = EventTracker()
    handler = create_web_handler(Profile(rules={}), tracker)

    handler(CaughtMessage(message=Message(title="orphan", severity="info", system="nowhere")))

    event = tracker.recent(1)[0]
    assert event.hold is False
    assert event.duration > 0


async def test_the_api_hands_hold_and_duration_to_the_browser():
    """The canvas reads /api/events; a field the API drops never arrives."""
    tracker = EventTracker()
    profile = _profile_with(
        dict(kind="static", text="{{ title }}", hold=True, duration=3, systems=["*"], severity=["info"])
    )
    create_web_handler(profile, tracker)(
        CaughtMessage(message=Message(title="7:5", severity="info", system="tabletennis"))
    )

    app = create_web_app(tracker, version="test", commit="c", date="d")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        payload = (await client.get("/api/events")).json()

    assert payload, "no events returned"
    assert payload[0]["hold"] is True
    assert payload[0]["duration"] == 3


def test_the_canvas_does_not_go_idle_while_an_event_is_held():
    """The template is the other half of this and has no test runner of its own.

    Asserting on its source is crude, but the alternative is a fix that is only
    half applied: the values reaching the browser and the canvas ignoring them,
    which is the state this replaced.
    """
    from pathlib import Path

    template = (Path(__file__).parent.parent / "src" / "led_catcher" / "web" / "templates" / "index.html").read_text()

    assert "if (ev.hold) return;" in template, "a held event still falls through to the idle timer"
    assert "ev.duration" in template, "the canvas is not using the event's own duration"
    assert "const EVENT_DISPLAY_SECONDS = 5;" not in template, "the hardcoded display duration is back"
