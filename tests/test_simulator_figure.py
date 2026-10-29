"""The winner's figure in the simulator (#148): a rule whose picture is an
https URL from the message, played as an overlay.

The canvas asks /api/preview/image for a bare file name only. Before the web
handler fetched the URL the way the panel's worker does, the canvas got a 400
and drew its static-text fallback, "---" in green, after every won set.
"""

from __future__ import annotations

from datetime import datetime

from httpx import ASGITransport, AsyncClient

from led_catcher.display import remote
from led_catcher.models import CaughtMessage, Message
from led_catcher.profile import DisplayConfig, Profile
from led_catcher.profile.engine import match_rule
from led_catcher.web import EventTracker, create_web_app, create_web_handler

URL = "https://sp.example/avatars/id-anna.gif"


def _profile() -> Profile:
    return Profile(
        rules={
            "figure": DisplayConfig(
                kind="gif",
                image="{{ tag('figure') }}",
                overlay=True,
                duration=4.6,
                systems=["tabletennis"],
                severity=["success"],
            ),
            "score": DisplayConfig(kind="static", text="{{ title }}", hold=True, systems=["tabletennis"]),
        }
    )


def _won() -> CaughtMessage:
    return CaughtMessage(
        message=Message(title="WIN 1:0", message="", severity="SUCCESS", system="tabletennis", tags=f"figure={URL}"),
        object_id="o",
        stream_id="1-0",
        caught_at=datetime.now(),
    )


def test_the_rule_matches_the_won_message():
    config = match_rule(_profile(), _won().message)
    assert config is not None and config.image == URL and config.then is not None


def test_a_figure_from_a_url_reaches_the_canvas_as_its_cached_file(monkeypatch):
    fetched = []
    monkeypatch.setattr(remote, "fetch_image", lambda url: fetched.append(url) or "url-abc.gif")
    tracker = EventTracker()

    create_web_handler(_profile(), tracker)(_won())

    event = tracker.recent(1)[0]
    assert (event.kind, event.image) == ("gif", "url-abc.gif"), "a bare name /api/preview/image serves"
    assert fetched == [URL]
    assert event.then is not None and (event.then.kind, event.then.text, event.then.hold) == ("static", "WIN 1:0", True)


def test_a_figure_that_cannot_be_fetched_gives_way_to_the_score(monkeypatch):
    def fail(url):
        raise remote.RemoteImageError("502 from sp.example", status=502)

    monkeypatch.setattr(remote, "fetch_image", fail)
    tracker = EventTracker()

    create_web_handler(_profile(), tracker)(_won())

    event = tracker.recent(1)[0]
    assert (event.kind, event.text, event.hold) == ("static", "WIN 1:0", True), "the score, not '---'"
    assert event.then is None


async def test_the_canvas_gets_what_the_overlay_gives_way_to(monkeypatch):
    monkeypatch.setattr(remote, "fetch_image", lambda url: "url-abc.gif")
    tracker = EventTracker()
    create_web_handler(_profile(), tracker)(_won())

    async with AsyncClient(transport=ASGITransport(app=create_web_app(tracker)), base_url="http://test") as c:
        event = (await c.get("/api/events")).json()[0]

    assert event["image"] == "url-abc.gif"
    assert (event["then"]["kind"], event["then"]["text"], event["then"]["hold"]) == ("static", "WIN 1:0", True)
    assert event["then"]["then"] is None
