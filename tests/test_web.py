"""Tests for the HTMX web simulator."""

import pytest
from httpx import ASGITransport, AsyncClient

from led_catcher.models import CaughtMessage, Message
from led_catcher.profile import Profile
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
async def test_web_app_empty_events():
    tracker = EventTracker()
    app = create_web_app(tracker)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/events")
    assert resp.status_code == 200
    assert "No events yet" in resp.text
