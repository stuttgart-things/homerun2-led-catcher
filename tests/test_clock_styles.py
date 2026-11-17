"""More faces for the idle screen: clock styles and colour modes (#162)."""

from __future__ import annotations

import base64
import time

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from led_catcher.config.settings import ApiConfig, load_config
from led_catcher.display.clock import IdleScreen, draw_idle
from led_catcher.display.clock_styles import (
    BINARY_COLS,
    BINARY_DOT,
    BINARY_ROW,
    DAYLIGHT,
    STYLES,
    daylight_color,
    render,
    resolve,
    word_lines,
)
from led_catcher.display.offscreen import ImageMatrix
from led_catcher.handlers.display_api import create_display_router
from led_catcher.web import EventTracker, create_web_app

TOKEN = "s3cret"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
ORANGE = (255, 160, 40)


def local(year, month, day, hour, minute, second) -> float:
    return time.mktime((year, month, day, hour, minute, second, 0, 0, -1))


# Tuesday 17 November 2026, 12:41:30.
NOW = local(2026, 11, 17, 12, 41, 30)


# ---- choosing the face ----------------------------------------------------------


def test_a_fixed_classic_clock_is_the_clock_it_always_was():
    face = resolve(IdleScreen(color=ORANGE), NOW)

    assert (face.style, face.color) == ("classic", ORANGE)
    assert face.until == local(2026, 11, 17, 12, 42, 0), "nothing changes before the next minute"


def test_the_same_configuration_shows_the_same_face_everywhere():
    """Panel and simulator are separate processes; they agree through the clock alone."""
    a = IdleScreen(color=ORANGE, style="rotate", color_mode="random", interval=300)
    b = IdleScreen(color=ORANGE, style="rotate", color_mode="random", interval=300)

    assert [resolve(a, NOW + n * 300) for n in range(20)] == [resolve(b, NOW + n * 300) for n in range(20)]


def test_rotate_plays_every_style_once_a_round():
    idle = IdleScreen(style="rotate", interval=60)
    first = int(NOW // 60) // len(STYLES) * len(STYLES)

    styles = [resolve(idle, (first + n) * 60 + 1).style for n in range(len(STYLES))]

    assert sorted(styles) == sorted(STYLES)


def test_rotate_and_random_change_at_the_slot_boundary():
    idle = IdleScreen(style="rotate", color_mode="random", interval=300)
    slot_end = (int(NOW // 300) + 1) * 300

    assert resolve(idle, NOW).until <= slot_end
    assert resolve(idle, NOW).color == resolve(idle, slot_end - 1).color, "one colour for the whole slot"
    colours = {resolve(idle, NOW + n * 300).color for n in range(10)}
    assert len(colours) > 1


def test_daylight_follows_the_time_of_day():
    hour, rgb = DAYLIGHT[2]
    at = local(2026, 11, 17, int(hour), int(hour % 1 * 60), 0)

    assert daylight_color(at) == rgb
    assert resolve(IdleScreen(color_mode="daylight"), at).color == rgb
    assert daylight_color(local(2026, 11, 17, 3, 0, 0)) != daylight_color(local(2026, 11, 17, 12, 0, 0))


# ---- the word clock -------------------------------------------------------------


@pytest.mark.parametrize(
    "hour, minute, lines",
    [
        (10, 0, ("ES IST", "ZEHN", "UHR")),
        (13, 2, ("ES IST", "EIN", "UHR")),
        (10, 15, ("ES IST", "VIERTEL", "NACH", "ZEHN")),
        (10, 25, ("ES IST", "FÜNF VOR", "HALB", "ELF")),
        (12, 30, ("ES IST", "HALB", "EINS")),
        (12, 41, ("ES IST", "ZWANZIG", "VOR", "EINS")),
        (23, 45, ("ES IST", "VIERTEL", "VOR", "ZWÖLF")),
        (0, 58, ("ES IST", "FÜNF VOR", "EINS")),
    ],
)
def test_word_lines(hour, minute, lines):
    assert word_lines(hour, minute) == lines


# ---- drawing --------------------------------------------------------------------


@pytest.mark.parametrize("style", [s for s in STYLES if s != "classic"])
def test_every_face_lights_the_panel_in_its_colour(style):
    image = render(style, ORANGE, NOW)

    assert image.size == (64, 64)
    assert ORANGE in {colour for _, colour in image.getcolors(64 * 64)}


def test_the_binary_clock_reads_12_41():
    image = render("binary", ORANGE, NOW)

    def lit(column: int, bit: int) -> bool:
        x = BINARY_COLS[column] + BINARY_DOT // 2
        y = 3 + (3 - bit) * BINARY_ROW + BINARY_DOT // 2
        return image.getpixel((x, y)) == ORANGE

    for column, digit in enumerate((1, 2, 4, 1)):
        assert [lit(column, bit) for bit in range(4)] == [bool(digit >> bit & 1) for bit in range(4)]


def test_draw_idle_puts_a_rendered_face_on_the_panel():
    matrix = ImageMatrix()

    until = draw_idle(matrix, IdleScreen(color=ORANGE, style="analog"), NOW)

    assert matrix.swaps == 1
    assert matrix.image.tobytes() == render("analog", ORANGE, NOW).tobytes()
    assert until == local(2026, 11, 17, 12, 42, 0)


def test_draw_idle_redraws_the_classic_clock_every_second_for_the_colon():
    matrix = ImageMatrix()

    assert draw_idle(matrix, IdleScreen(color=ORANGE), NOW) == NOW + 1


# ---- configuration --------------------------------------------------------------


def test_the_environment_picks_the_face(monkeypatch):
    monkeypatch.setenv("LED_IDLE_STYLE", "Rotate")
    monkeypatch.setenv("LED_IDLE_COLOR_MODE", "daylight")
    monkeypatch.setenv("LED_IDLE_INTERVAL", "120")

    cfg = load_config()

    assert (cfg.idle_style, cfg.idle_color_mode, cfg.idle_interval) == ("rotate", "daylight", 120)


@pytest.mark.parametrize(
    "key, value",
    [("LED_IDLE_STYLE", "sundial"), ("LED_IDLE_COLOR_MODE", "rainbow"), ("LED_IDLE_INTERVAL", "5")],
)
def test_an_unknown_face_fails_startup(monkeypatch, key, value):
    monkeypatch.setenv(key, value)

    with pytest.raises(ValueError, match=key):
        load_config()


# ---- the API --------------------------------------------------------------------


class FakeWorker:
    def __init__(self) -> None:
        self.showing = None
        self.idle = None
        self.idle_active = False
        self.playlist = None
        self.playlist_active = False
        self.queue_size = 1
        self.pending = 0
        self.superseded = 0

    def set_idle(self, idle) -> None:
        self.idle = idle
        self.idle_active = idle is not None


def client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def make_app(worker, idle_face=None) -> FastAPI:
    app = FastAPI()
    app.include_router(create_display_router(worker, ApiConfig(token=TOKEN), idle_face=idle_face))
    return app


async def test_put_idle_sets_the_face_and_get_display_reports_it():
    worker = FakeWorker()
    async with client(make_app(worker)) as c:
        put = await c.put(
            "/display/idle",
            json={"mode": "clock", "style": "rotate", "colorMode": "random", "interval": 120},
            headers=AUTH,
        )
        body = (await c.get("/display")).json()

    assert put.status_code == 200
    assert worker.idle.style == "rotate" and worker.idle.color_mode == "random" and worker.idle.interval == 120
    assert (body["idle"]["style"], body["idle"]["colorMode"], body["idle"]["interval"]) == ("rotate", "random", 120)


async def test_put_idle_keeps_the_face_when_none_is_given():
    worker = FakeWorker()
    async with client(make_app(worker, idle_face={"style": "words", "color_mode": "daylight", "interval": 300})) as c:
        await c.put("/display/idle", json={"mode": "clock"}, headers=AUTH)
        await c.put("/display/idle", json={"mode": "off"}, headers=AUTH)
        await c.put("/display/idle", json={"mode": "clock", "colorMode": "fixed"}, headers=AUTH)

    assert (worker.idle.style, worker.idle.color_mode) == ("words", "fixed")


@pytest.mark.parametrize(
    "body",
    [{"mode": "clock", "style": "sundial"}, {"mode": "clock", "colorMode": "x"}, {"mode": "clock", "interval": 1}],
)
async def test_put_idle_rejects_an_unknown_face(body):
    async with client(make_app(FakeWorker())) as c:
        response = await c.put("/display/idle", json=body, headers=AUTH)

    assert response.status_code == 422


async def test_the_options_list_the_styles_and_colour_modes():
    async with client(make_app(FakeWorker())) as c:
        body = (await c.get("/display/options")).json()

    assert body["idleStyles"] == [*STYLES, "rotate"]
    assert body["idleColorModes"] == ["fixed", "daylight", "random"]


# ---- the preview the canvases draw ----------------------------------------------


def web_client() -> AsyncClient:
    return client(create_web_app(EventTracker()))


async def test_the_preview_hands_the_canvas_the_panels_frame():
    async with web_client() as c:
        body = (await c.get("/api/preview/clock", params={"style": "big", "color": "255,160,40"})).json()

    assert body["style"] == "big" and body["color"] == list(ORANGE)
    assert 0 < body["validFor"] <= 60
    frame = base64.b64decode(body["frames"][0])
    assert len(frame) == 64 * 64 * 3


async def test_the_classic_preview_has_no_frame_the_canvas_draws_it():
    async with web_client() as c:
        body = (await c.get("/api/preview/clock", params={"color": "1,2,3"})).json()

    assert body["style"] == "classic" and body["color"] == [1, 2, 3]
    assert "frames" not in body


@pytest.mark.parametrize("params", [{"color": "300,0,0"}, {"color": "red"}, {"style": "sundial"}, {"interval": "1"}])
async def test_the_preview_rejects_what_the_panel_cannot_show(params):
    async with web_client() as c:
        response = await c.get("/api/preview/clock", params=params)

    assert response.status_code in (400, 422)
