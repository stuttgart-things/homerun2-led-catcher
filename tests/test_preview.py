"""The simulator's preview routes (#83): the canvas gets the panel's own inputs."""

from __future__ import annotations

import base64
import os
import tempfile

import pytest
from httpx import ASGITransport, AsyncClient
from PIL import BdfFontFile, Image, ImageDraw, ImageFont

from led_catcher.display.bdf import load_glyphs, load_metrics
from led_catcher.display.frames import load_animation
from led_catcher.display.modes import _resolve_font, _resolve_image
from led_catcher.models import CaughtMessage, Message
from led_catcher.profile import DisplayConfig, Profile
from led_catcher.web import EventTracker, create_web_app, create_web_handler

FONTS = ["4x6.bdf", "6x10.bdf", "7x13.bdf"]


def client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=create_web_app(EventTracker())), base_url="http://test")


def render_with_glyphs(font_path: str, text: str) -> Image.Image:
    """Draw `text` the way the canvas does, from the glyph data it is sent."""
    glyphs = load_glyphs(font_path)
    metrics = load_metrics(font_path)
    img = Image.new("1", (metrics.text_width(text) + 8, metrics.height + 8))
    pen, baseline = 0, metrics.baseline + 4
    for char in text:
        g = glyphs.get(ord(char))
        if g is None:
            continue
        top = baseline - g.height - g.y_offset
        for r, bits in enumerate(g.rows):
            for c in range(g.width):
                if bits >> (g.width - 1 - c) & 1 and 0 <= g.x_offset + c < g.advance:
                    img.putpixel((pen + g.x_offset + c, top + r), 1)
        pen += g.advance
    return img


def render_with_pillow(font_path: str, text: str) -> Image.Image:
    with open(font_path, "rb") as handle:
        bdf = BdfFontFile.BdfFontFile(handle)
    target = os.path.join(tempfile.mkdtemp(), "font")
    bdf.save(target)
    font = ImageFont.load(target + ".pil")
    img = Image.new("1", (400, 40))
    ImageDraw.Draw(img).text((0, 0), text, font=font, fill=1)
    return img


def cropped(img: Image.Image) -> bytes:
    return img.crop(img.getbbox()).tobytes()


@pytest.mark.parametrize("font", FONTS)
def test_glyph_bitmaps_match_pillows_reading_of_the_font(font):
    # An independent BDF reader, so a bug in load_glyphs cannot hide behind
    # a matching bug in the test.
    path = _resolve_font(font)
    text = "HELLO PANEL 12:04 gjpqy"
    assert cropped(render_with_glyphs(path, text)) == cropped(render_with_pillow(path, text))


@pytest.mark.parametrize("font", FONTS)
def test_glyph_advances_are_the_metrics_the_panel_scrolls_by(font):
    path = _resolve_font(font)
    metrics = load_metrics(path)
    for codepoint, glyph in load_glyphs(path).items():
        assert glyph.advance == metrics.advances[codepoint]


async def test_text_preview_carries_the_glyphs_and_font_box():
    async with client() as c:
        res = await c.get("/api/preview/text", params={"font": "6x10.bdf", "text": "HÄ HÄ"})

    assert res.status_code == 200
    body = res.json()
    metrics = load_metrics(_resolve_font("6x10.bdf"))
    assert (body["font"], body["height"], body["baseline"]) == ("6x10.bdf", metrics.height, metrics.baseline)
    assert body["width"] == metrics.text_width("HÄ HÄ")
    assert set(body["glyphs"]) == {str(ord("H")), str(ord("Ä")), str(ord(" "))}
    advance, width, height, x_offset, y_offset, rows = body["glyphs"][str(ord("H"))]
    assert advance == 6 and len(rows) == height


async def test_text_preview_falls_back_to_the_default_font_like_the_panel():
    async with client() as c:
        body = (await c.get("/api/preview/text", params={"font": "missing.bdf", "text": "x"})).json()

    assert body["font"] == "6x10.bdf"


@pytest.mark.parametrize("font", ["../fonts/6x10.bdf", "/etc/passwd", "a/b.bdf"])
async def test_text_preview_rejects_paths(font):
    async with client() as c:
        res = await c.get("/api/preview/text", params={"font": font, "text": "x"})
    assert res.status_code == 400


async def test_image_preview_is_the_panels_own_frames():
    async with client() as c:
        body = (await c.get("/api/preview/image/sunset.gif")).json()

    animation = load_animation(_resolve_image("sunset.gif"), 64, 64)
    assert body["animated"] is True
    assert body["delays"] == list(animation.delays)
    assert len(body["frames"]) == len(animation.frames)
    assert base64.b64decode(body["frames"][3]) == animation.frames[3].tobytes()


async def test_a_still_image_is_one_frame():
    async with client() as c:
        body = (await c.get("/api/preview/image/test-pattern.png")).json()

    assert body["animated"] is False
    assert len(base64.b64decode(body["frames"][0])) == 64 * 64 * 3


@pytest.mark.parametrize(("name", "code"), [("nope.gif", 404), (".hidden.png", 400), ("a b.png", 400)])
async def test_image_preview_rejects_missing_and_paths(name, code):
    async with client() as c:
        res = await c.get(f"/api/preview/image/{name}")
    assert res.status_code == code


# ---- events carry what the canvas draws ------------------------------------------


def _caught(system="demo", severity="info", title="the title", message="the body") -> CaughtMessage:
    return CaughtMessage(message=Message(system=system, severity=severity, title=title, message=message))


def test_a_matched_message_records_the_rules_text_font_and_image():
    profile = Profile(
        rules={
            "r": DisplayConfig(
                kind="gif", text="{{ system }}: {{ title }}", font="7x13.bdf", image="sunset.gif", systems=["demo"]
            )
        }
    )
    tracker = EventTracker()
    create_web_handler(profile, tracker)(_caught())

    event = tracker.recent(1)[0]
    assert (event.kind, event.text, event.font, event.image, event.matched) == (
        "gif",
        "demo: the title",
        "7x13.bdf",
        "sunset.gif",
        True,
    )
    assert event.title == "the title"  # the timeline keeps the message title


def test_an_unmatched_message_is_not_drawn():
    tracker = EventTracker()
    create_web_handler(Profile(), tracker)(_caught())
    assert tracker.recent(1)[0].matched is False


def test_events_get_increasing_ids():
    tracker = EventTracker()
    handler = create_web_handler(Profile(), tracker)
    handler(_caught())
    handler(_caught())
    newest, older = tracker.recent(2)
    assert newest.id == older.id + 1


async def test_api_events_expose_the_drawing_fields():
    tracker = EventTracker()
    create_web_handler(Profile(), tracker)(_caught())
    app = create_web_app(tracker)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        (event,) = (await c.get("/api/events")).json()

    assert {"text", "font", "image", "matched", "at", "id"} <= set(event)
    assert event["matched"] is False
