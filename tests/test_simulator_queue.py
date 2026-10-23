"""The canvas queues like the display worker, at the same DISPLAY_QUEUE (#144).

In ``LED_MODE=web`` there is no worker: the simulator's canvas and ``/wall`` are
the only picture of the panel, and they used to model the single slot whatever
``DISPLAY_QUEUE`` said — dropping the middle display of a burst the panel shows.

The queueing itself is JavaScript with no test runner of its own. What is tested
here is that the configured length reaches both pages, and that their worker is
the queue and not the slot.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from led_catcher.__main__ import _build_app
from led_catcher.config.settings import Config, RedisConfig
from led_catcher.profile import Profile
from led_catcher.web import EventTracker, create_web_app

TEMPLATES = Path(__file__).parent.parent / "src" / "led_catcher" / "web" / "templates"
PAGES = ("/", "/wall")


async def _get(app, path: str) -> str:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get(path)
    assert res.status_code == 200
    return res.text


def _queue_length(page: str) -> str:
    match = re.search(r"const DISPLAY_QUEUE = Math\.max\(1, parseInt\('([^']*)', 10\) \|\| 1\);", page)
    assert match, "the page does not read DISPLAY_QUEUE"
    return match.group(1)


@pytest.mark.parametrize("path", PAGES)
async def test_the_page_defaults_to_a_single_slot(path):
    page = await _get(create_web_app(EventTracker()), path)
    assert _queue_length(page) == "1"
    assert "{{" not in page, "a placeholder was left unreplaced"


@pytest.mark.parametrize("path", PAGES)
async def test_the_page_is_handed_the_configured_queue(path):
    page = await _get(create_web_app(EventTracker(), display_queue=3), path)
    assert _queue_length(page) == "3"


@pytest.mark.parametrize("path", PAGES)
async def test_web_mode_hands_display_queue_to_the_canvas(path):
    """The case this is for: no worker, the canvas is all there is."""
    cfg = Config(redis=RedisConfig(), display_queue=3)
    page = await _get(_build_app(cfg, "web", EventTracker(), None, Profile()), path)
    assert _queue_length(page) == "3"


def test_a_queue_shorter_than_one_is_refused():
    with pytest.raises(ValueError, match="at least 1"):
        create_web_app(EventTracker(), display_queue=0)


@pytest.mark.parametrize("name", ["index.html", "wall.html"])
def test_the_canvas_worker_is_a_queue(name):
    """Crude, like the other template checks: the alternative is the length
    reaching the page and the canvas still overwriting a single slot."""
    template = (TEMPLATES / name).read_text()
    assert "let pending = [];" in template
    assert "enqueue(pending, ev, DISPLAY_QUEUE);" in template
    assert "pending = ev;" not in template, "the single slot is back"
    assert "const ev = pending.shift();" in template


def test_both_pages_queue_with_the_same_rules():
    def enqueue(name: str) -> str:
        template = (TEMPLATES / name).read_text()
        match = re.search(r"function enqueue\(queue, ev, size\) \{.*?\n        \}", template, re.S)
        assert match, f"{name} has no enqueue()"
        return match.group(0)

    assert enqueue("index.html") == enqueue("wall.html")
