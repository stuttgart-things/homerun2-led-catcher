"""The micro-frontend contract with homerun2-portal: a manifest and fragments.

The portal loads each fragment the manifest lists into its own page, on its
own origin. So every URL in a fragment must carry BASE_PATH, the fragment must
be a bare partial, and everything from a message must be escaped.
"""

from __future__ import annotations

import re

from httpx import ASGITransport, AsyncClient

from led_catcher.__main__ import _mount_base_path
from led_catcher.web import EventTracker, LedEvent, create_web_app
from led_catcher.web.fragments import EVENTS_LIMIT, render_events_fragment


def _tracker(n: int = 1) -> EventTracker:
    tracker = EventTracker()
    for i in range(n):
        tracker.record(LedEvent(timestamp=f"12:00:{i:02d}", severity="error", system="ci", title=f"build {i} <failed>"))
    return tracker


async def _get(app, path: str):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.get(path)


async def test_manifest_lists_the_events_fragment_under_the_base_path():
    app = _mount_base_path(create_web_app(_tracker(), version="v1.2.3", base_path="/led"), "/led")
    resp = await _get(app, "/led/ui/manifest.json")
    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "LED Matrix"
    assert body["version"] == "v1.2.3"
    assert body["basePath"] == "/led"
    assert body["fragments"] == [
        {"id": "events", "title": "Latest on the matrix", "url": "/led/ui/fragment/events", "refreshSeconds": 5}
    ]


async def test_manifest_at_the_root_without_a_base_path():
    resp = await _get(create_web_app(_tracker()), "/ui/manifest.json")
    assert resp.json()["fragments"][0]["url"] == "/ui/fragment/events"


async def test_events_fragment_is_a_bare_escaped_partial():
    app = _mount_base_path(create_web_app(_tracker(), base_path="/led"), "/led")
    resp = await _get(app, "/led/ui/fragment/events")
    assert resp.status_code == 200
    page = resp.text
    assert "<html" not in page and "<head" not in page and "<script" not in page
    assert 'class="hr-list"' in page
    assert 'class="hr-sev hr-sev-error"' in page
    assert "build 0 &lt;failed&gt;" in page
    # Every same-origin URL carries the prefix: the fragment runs on the portal's origin.
    urls = re.findall(r'(?:href|src|hx-get|hx-post)="(/[^"]*)"', page)
    assert urls == ["/led/"]


def test_events_fragment_is_capped_and_newest_first():
    tracker = _tracker(EVENTS_LIMIT + 3)
    page = render_events_fragment(tracker.recent(EVENTS_LIMIT))
    assert page.count('<li class="hr-item">') == EVENTS_LIMIT
    assert page.index(f"build {EVENTS_LIMIT + 2} ") < page.index(f"build {EVENTS_LIMIT + 1} ")


def test_empty_fragment_says_so():
    assert "No events yet." in render_events_fragment([])


def test_unknown_severity_falls_back_to_info():
    page = render_events_fragment([LedEvent(severity="<weird>", title="x")])
    assert "hr-sev-info" in page
    assert "&lt;WEIRD&gt;" in page
