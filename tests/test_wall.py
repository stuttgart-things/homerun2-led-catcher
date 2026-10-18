"""The big-screen view (/wall): the events on a larger virtual matrix, for a TV.

The page's renderer is JavaScript with no test runner of its own; what is
tested here is what it is handed — frames at the grid's size, the scoreboard
as the panel draws it, the card's uncut content — and that serving it cannot
cost the panel anything.
"""

from __future__ import annotations

import base64

import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image

from led_catcher.display import frames as frames_mod
from led_catcher.display.card import CardContent
from led_catcher.display.frames import clear_animation_cache, load_animation
from led_catcher.display.offscreen import ImageMatrix
from led_catcher.display.score import parse_score, render
from led_catcher.models import Message
from led_catcher.profile import DisplayConfig, Profile
from led_catcher.web import EventTracker, create_web_app
from led_catcher.web.handler import build_event
from led_catcher.web.preview import MAX_PREVIEW_HEIGHT, MAX_PREVIEW_WIDTH

SCORE = "points=7:5;sets=1:0;set=2;a=anna;b=ben;serve=a"


def client(tracker: EventTracker | None = None, **kwargs) -> AsyncClient:
    app = create_web_app(tracker or EventTracker(), **kwargs)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture(autouse=True)
def fresh_caches():
    clear_animation_cache()
    yield
    clear_animation_cache()


@pytest.fixture
def visual_aid(tmp_path, monkeypatch):
    monkeypatch.setenv("VISUAL_AID_DIR", str(tmp_path))
    return tmp_path


def checkerboard(size: int) -> Image.Image:
    image = Image.new("RGB", (size, size))
    for y in range(size):
        for x in range(size):
            if (x + y) % 2:
                image.putpixel((x, y), (255, 255, 255))
    return image


async def test_the_wall_is_served_uncached_like_the_simulator():
    async with client(version="v1.2.3", commit="abcdef0123", mode="full") as c:
        res = await c.get("/wall")
    assert res.status_code == 200
    assert res.headers.get("cache-control") == "no-store"
    assert '<canvas id="wall">' in res.text
    assert "v1.2.3" in res.text and "abcdef0" in res.text and "abcdef0123" not in res.text
    assert "{{" not in res.text, "a placeholder was left unreplaced"


async def test_the_wall_opens_one_event_stream():
    async with client() as c:
        page = (await c.get("/wall")).text
    assert page.count("new EventSource(") == 1


async def test_the_simulator_links_to_the_wall():
    async with client() as c:
        page = (await c.get("/")).text
    assert 'href="/wall"' in page


async def test_the_wall_and_the_preview_agree_on_the_largest_grid():
    async with client() as c:
        page = (await c.get("/wall")).text
    assert f"const MAX_W = {MAX_PREVIEW_WIDTH}, MAX_H = {MAX_PREVIEW_HEIGHT};" in page


async def test_an_image_is_prepared_at_the_size_asked_for(visual_aid):
    Image.new("RGB", (40, 20), (200, 10, 10)).save(visual_aid / "wide.png")
    async with client() as c:
        res = await c.get("/api/preview/image/wide.png", params={"width": 256, "height": 144})
    assert res.status_code == 200
    data = res.json()
    assert (data["width"], data["height"]) == (256, 144)
    assert len(base64.b64decode(data["frames"][0])) == 256 * 144 * 3


async def test_without_a_size_the_preview_is_the_panels():
    async with client() as c:
        data = (await c.get("/api/preview/image/sunset.gif")).json()
    assert (data["width"], data["height"]) == (64, 64)


@pytest.mark.parametrize(
    "params",
    [
        {"width": MAX_PREVIEW_WIDTH + 1, "height": 64},
        {"width": 64, "height": MAX_PREVIEW_HEIGHT + 1},
        {"width": 0, "height": 64},
    ],
)
async def test_a_grid_outside_the_bounds_is_refused(params):
    async with client() as c:
        res = await c.get("/api/preview/image/sunset.gif", params=params)
    assert res.status_code == 422


async def test_wall_frames_do_not_evict_the_panels(visual_aid):
    """The panel's cache is small and a Pi re-decoding a GIF is slow.

    A TV switching grid sizes must not push the panel's frames out.
    """
    Image.new("RGB", (64, 64), (0, 200, 0)).save(visual_aid / "panel.png")
    load_animation(visual_aid / "panel.png", 64, 64)
    before = frames_mod._prepare.cache_info().currsize

    async with client() as c:
        for rows in range(100, 100 + frames_mod.ANIMATION_CACHE_SIZE + 2):
            res = await c.get("/api/preview/image/panel.png", params={"width": 200, "height": rows})
            assert res.status_code == 200

    assert frames_mod._prepare.cache_info().currsize == before
    hits = frames_mod._prepare.cache_info().hits
    load_animation(visual_aid / "panel.png", 64, 64)
    assert frames_mod._prepare.cache_info().hits == hits + 1, "the panel's frames were decoded again"


async def test_a_small_image_is_enlarged_pixel_for_pixel_on_the_wall(visual_aid):
    """Smoothed up, a 1px checkerboard is a grey blur; on the wall it stays one."""
    checkerboard(16).save(visual_aid / "check.png")
    async with client() as c:
        data = (await c.get("/api/preview/image/check.png", params={"width": 64, "height": 48})).json()
    frame = Image.frombytes("RGB", (64, 48), base64.b64decode(data["frames"][0]))
    # 16px into 48 rows: 3x, centred in the 64 columns.
    image = frame.crop((8, 0, 56, 48))
    assert {color for _, color in image.getcolors()} == {(0, 0, 0), (255, 255, 255)}
    assert image.getpixel((0, 0)) == (0, 0, 0)
    assert image.getpixel((3, 0)) == (255, 255, 255)


def test_the_panel_still_smooths_its_own_frames(visual_aid):
    checkerboard(16).save(visual_aid / "check.png")
    frame = load_animation(visual_aid / "check.png", 64, 64).frames[0]
    assert len(frame.getcolors(4096)) > 2


async def test_the_score_preview_is_the_panels_own_scoreboard():
    async with client() as c:
        res = await c.get("/api/preview/score", params={"text": SCORE})
    assert res.status_code == 200
    data = res.json()
    assert (data["width"], data["height"], data["animated"]) == (64, 64, False)

    matrix = ImageMatrix(64, 64)
    render(matrix, parse_score(SCORE))
    assert base64.b64decode(data["frames"][0]) == matrix.image.tobytes()


async def test_a_text_that_is_no_score_is_refused_so_the_wall_shows_the_text():
    async with client() as c:
        res = await c.get("/api/preview/score", params={"text": "hello"})
    assert res.status_code == 404


def test_a_card_event_carries_its_content_uncut():
    """The 64-column lines are cut short; the wall lays the card out again."""
    title = "Pod crash loop detected in namespace monitoring on node worker-3"
    content = CardContent(system="prometheus", title=title, message="OOMKilled", author="am", time="15:42")
    config = DisplayConfig(kind="card", color=(255, 0, 0))
    config.card = content
    event = build_event(Profile(), Message(title=title, system="prometheus"), config)

    assert event.card["content"]["title"] == title
    assert event.card["content"] == {
        "system": "prometheus",
        "title": title,
        "message": "OOMKilled",
        "author": "am",
        "time": "15:42",
        "tags": "",
    }
    assert all(title != line["text"] for line in event.card["lines"]), "the panel's lines are no longer cut"
