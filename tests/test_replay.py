"""A click in the timeline shows an event on the matrix again (#128)."""

from datetime import datetime, time

from httpx import ASGITransport, AsyncClient

from led_catcher.models import CaughtMessage, Message
from led_catcher.profile.engine import FALLBACK_CARD_SECONDS, DisplayConfig, Profile, QuietHours
from led_catcher.web import EventTracker, LedEvent, create_replayer, create_web_app, create_web_handler
from led_catcher.web.app import _render_events

MSG = Message(title="Deploy failed", message="argocd sync error", severity="error", system="argocd", tags="ci")


def _profile(**kw) -> Profile:
    return Profile(rules={"errors": DisplayConfig(kind="card", systems=["*"], severity=["error"], duration=20)}, **kw)


class FakeWorker:
    def __init__(self):
        self.submitted = []

    def submit(self, config):
        self.submitted.append(config)


def _caught(tracker, profile, msg=MSG) -> int:
    create_web_handler(profile, tracker)(CaughtMessage(message=msg))
    return tracker.recent(1)[0].id


def test_replay_records_a_new_event_and_drives_the_panel():
    tracker, profile, worker = EventTracker(), _profile(), FakeWorker()
    first = _caught(tracker, profile)

    event = create_replayer(profile, tracker, worker)(first)

    assert event.id != first
    assert event.replay_of == first
    assert tracker.recent(1)[0] is event
    assert event.kind == "card" and event.duration == 20
    assert [c.kind for c in worker.submitted] == ["card"]


def test_replaying_a_replay_points_at_the_original():
    tracker, profile = EventTracker(), _profile()
    first = _caught(tracker, profile)
    replay = create_replayer(profile, tracker)

    again = replay(replay(first).id)

    assert again.replay_of == first


def test_replay_ignores_quiet_hours():
    # Quiet all day, nothing allowed: the original was not shown.
    quiet = QuietHours(start=time(0, 0), end=time(23, 59), allow=[])
    tracker, profile = EventTracker(), _profile(quiet_hours=quiet)
    first = _caught(tracker, profile)
    assert tracker.get(first).matched is False

    event = create_replayer(profile, tracker)(first)

    assert event.matched is True
    assert event.duration == 20


def test_an_unmatched_event_is_shown_as_a_card_in_its_colour():
    tracker, profile, worker = EventTracker(), Profile(), FakeWorker()
    first = _caught(tracker, profile, Message(title="T", message="M", severity="warning", system="x"))

    event = create_replayer(profile, tracker, worker)(first)

    assert event.kind == "card"
    assert event.duration == FALLBACK_CARD_SECONDS
    assert event.color == profile.colors["warning"]
    assert worker.submitted[0].kind == "card"


def test_unknown_and_api_written_events_are_not_replayed():
    tracker, profile = EventTracker(), _profile()
    tracker.record(LedEvent(title="from POST /display"))  # no source
    api_event = tracker.recent(1)[0].id
    replay = create_replayer(profile, tracker)

    assert replay(api_event) is None
    assert replay(9999) is None


def test_rows_are_clickable_only_with_replay_and_a_source():
    tracker, profile = EventTracker(), _profile()
    first = _caught(tracker, profile)
    tracker.record(LedEvent(title="api"))

    html = _render_events(tracker, replayable=True)
    assert f'hx-post="/api/events/{first}/replay"' in html
    assert html.count("hx-post") == 1

    assert "hx-post" not in _render_events(tracker, replayable=False)


def test_a_replayed_row_is_marked():
    tracker, profile = EventTracker(), _profile()
    create_replayer(profile, tracker)(_caught(tracker, profile))

    assert "↻" in _render_events(tracker, replayable=True)


async def test_the_replay_endpoint():
    tracker, profile = EventTracker(), _profile()
    first = _caught(tracker, profile)
    app = create_web_app(tracker, replay=create_replayer(profile, tracker), replay_rate_limit=2)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ok = await client.post(f"/api/events/{first}/replay")
        missing = await client.post("/api/events/9999/replay")
        limited = await client.post(f"/api/events/{first}/replay")

    assert ok.status_code == 202
    assert ok.json()["replayOf"] == first
    assert missing.status_code == 404
    assert limited.status_code == 429
    assert "Retry-After" in limited.headers


async def test_no_replay_endpoint_without_a_replayer():
    app = create_web_app(EventTracker())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.post("/api/events/1/replay")).status_code in (404, 405)


def test_now_param_unaffected(monkeypatch):
    # match_rule keeps honouring quiet hours for caught messages.
    from led_catcher.profile import match_rule

    quiet = QuietHours(start=time(0, 0), end=time(23, 59), allow=[])
    assert match_rule(_profile(quiet_hours=quiet), MSG, now=datetime(2026, 10, 9, 12, 0)) is None
