"""BASE_PATH: the service behind a reverse proxy under a prefix such as /led.

The homerun2 portal forwards /led/... to the service unchanged. So the app
must answer under the prefix, and every URL its page, partials and event
stream point at must carry it — one that does not lands on the portal's root.
"""

from __future__ import annotations

import re

import pytest
from httpx import ASGITransport, AsyncClient

from led_catcher.__main__ import _build_app, _mount_base_path
from led_catcher.config.settings import Config, RedisConfig, parse_base_path
from led_catcher.profile import Profile
from led_catcher.web import EventTracker, LedEvent, create_web_app
from led_catcher.web.app import PORTAL_TOKENS_LINK, _render_events, _render_streams_control, event_stream


class _Consumer:
    streams = ["messages"]
    configured_streams = ["messages"]
    is_overridden = False


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("", ""),
        ("/", ""),
        ("  ", ""),
        ("/led", "/led"),
        ("led", "/led"),
        ("/led/", "/led"),
        ("/homerun2/led-catcher", "/homerun2/led-catcher"),
    ],
)
def test_parse_base_path(raw, expected):
    assert parse_base_path(raw) == expected


@pytest.mark.parametrize("raw", ["/led catcher", "/led/../admin", "/<script>", "/led?x=1", "/a//b", "/'"])
def test_parse_base_path_rejects_anything_but_plain_segments(raw):
    with pytest.raises(ValueError, match="BASE_PATH"):
        parse_base_path(raw)


def test_load_config_reads_base_path(monkeypatch):
    from led_catcher.config import load_config

    monkeypatch.setenv("BASE_PATH", "/led/")
    assert load_config().base_path == "/led"


def _tracker() -> EventTracker:
    tracker = EventTracker()
    tracker.record(LedEvent(timestamp="12:00:00", severity="error", system="ci", title="build failed", source=object()))
    return tracker


def _app(base_path: str):
    return create_web_app(
        _tracker(), consumer=_Consumer(), presets=[["messages"]], replay=lambda _id: None, base_path=base_path
    )


# Absolute same-origin URLs in a page: URL attributes, and string literals in
# the script that name a path ("/api/...", not "//" or a lone "/" glyph key)
# and are passed to fetch(), EventSource or a helper, or assigned. A literal
# joined to BASE_PATH ("BASE_PATH + '/api'") is not preceded by any of these.
_ROOT_URL = re.compile(
    r"""(?:sse-connect|hx-get|hx-post|hx-put|hx-delete|href|src|action)="(/(?!/)[^"]*)"""
    r"""|[(=,]\s*['"](/[A-Za-z][^"'\s]*)"""
)


def _unprefixed(page: str) -> list[str]:
    """URLs in ``page`` that miss the /led prefix; the BASE_PATH constant itself is fine."""
    urls = [attr or literal for attr, literal in _ROOT_URL.findall(page)]
    return [url for url in urls if url != "/led" and not url.startswith("/led/")]


async def test_page_points_every_url_at_the_base_path():
    app = _mount_base_path(_app("/led"), "/led")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/led/")
    assert resp.status_code == 200
    page = resp.text
    assert "{{ base_path }}" not in page
    assert 'sse-connect="/led/api/events/stream"' in page
    assert "const BASE_PATH = '/led';" in page
    assert 'hx-post="/led/api/events/' in page
    assert 'hx-post="/led/ui/streams"' in page
    assert 'href="/led/wall"' in page
    assert not _unprefixed(page)


async def test_behind_the_portal_the_page_links_the_portal_tokens():
    """The one root URL a page hands out on purpose: the portal's own tokens file."""
    app = _mount_base_path(_app("/led"), "/led")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        alone = (await client.get("/led/")).text
        behind = (await client.get("/led/", headers={"X-Forwarded-Prefix": "/led"})).text
    assert "{{ portal_theme }}" not in alone
    assert PORTAL_TOKENS_LINK not in alone
    assert behind.count(PORTAL_TOKENS_LINK) == 1
    assert behind.index(PORTAL_TOKENS_LINK) > behind.index("</style>")
    assert _unprefixed(behind) == ["/static/homerun2-tokens.css"]


async def test_the_page_switches_to_the_compact_header_inside_the_portal():
    """In the portal's iframe a script sets hr-embedded before the first paint; the CSS keys on it."""
    app = _mount_base_path(_app("/led"), "/led")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        page = (await client.get("/led/")).text
    head = page.split("</head>")[0]
    assert "window.self !== window.top" in head
    assert head.index("classList.add('hr-embedded')") < head.index("<style>")
    assert ".hr-embedded .header {" in head
    assert '<span class="header-service">' in page


async def test_wall_points_every_url_at_the_base_path():
    app = _mount_base_path(_app("/led"), "/led")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/led/wall")
    assert resp.status_code == 200
    page = resp.text
    assert "{{ base_path }}" not in page
    assert "const BASE_PATH = '/led';" in page
    assert 'href="/led/"' in page
    assert not _unprefixed(page)


async def test_without_a_base_path_the_page_is_unchanged():
    app = _mount_base_path(_app(""), "")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        page = (await client.get("/")).text
    assert 'sse-connect="/api/events/stream"' in page
    assert "const BASE_PATH = '';" in page
    assert 'hx-post="/ui/streams"' in page


async def test_routes_answer_under_the_base_path():
    app = _mount_base_path(_app("/led"), "/led")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        events = await client.get("/led/api/events")
        partial = await client.get("/led/events")
        streams = await client.get("/led/ui/streams")
        unprefixed = await client.get("/api/events")
    assert events.status_code == 200
    assert events.json()[0]["title"] == "build failed"
    assert 'hx-post="/led/api/events/' in partial.text
    assert 'hx-post="/led/ui/streams"' in streams.text
    assert unprefixed.status_code == 404


async def test_root_redirects_to_the_ui_and_health_stays_at_the_root():
    app = _mount_base_path(_app("/led"), "/led")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        root = await client.get("/")
        bare = await client.get("/led")
        healthz = await client.get("/healthz")
        health = await client.get("/health")
        prefixed = await client.get("/led/healthz")
    assert root.status_code == 307
    assert root.headers["location"] == "/led/"
    assert bare.status_code in (307, 308)
    assert bare.headers["location"].endswith("/led/")
    # Probes in kcl/deploy.k hit /healthz without the prefix.
    assert healthz.status_code == 200
    assert health.status_code == 200
    # _app() does not register the health routes itself; _build_app does.
    assert prefixed.status_code == 404


async def test_build_app_serves_everything_under_the_base_path():
    cfg = Config(redis=RedisConfig(), base_path="/led")
    app = _build_app(cfg, "web", EventTracker(), None, Profile())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        page = await client.get("/led/")
        display = await client.get("/led/display")
        healthz = await client.get("/led/healthz")
        root_healthz = await client.get("/healthz")
    assert page.status_code == 200
    assert "const BASE_PATH = '/led';" in page.text
    assert display.status_code == 200
    assert healthz.status_code == 200
    assert root_healthz.status_code == 200


def test_partials_carry_the_base_path():
    events = _render_events(_tracker(), replayable=True, base_path="/led")
    assert 'hx-post="/led/api/events/' in events

    control = _render_streams_control(_Consumer(), [["messages"], ["scores"]], base_path="/led")
    assert control.count('hx-post="/led/ui/streams"') == 2


async def test_event_stream_partials_carry_the_base_path():
    async def connected() -> bool:
        return False

    stream = event_stream(_tracker(), connected, _Consumer(), [["messages"]], replayable=True, base_path="/led")
    sent = [await anext(stream) for _ in range(3)]
    await stream.aclose()
    by_event = {e["event"]: e["data"] for e in sent}
    assert 'hx-post="/led/api/events/' in by_event["events-update"]
    assert 'hx-post="/led/ui/streams"' in by_event["streams-update"]
