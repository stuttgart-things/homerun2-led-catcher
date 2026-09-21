"""The idle screen: a clock whenever nothing else is on the panel (#115)."""

from __future__ import annotations

import threading
import time

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from led_catcher.__main__ import _idle_screen
from led_catcher.config.settings import ApiConfig, Config, RedisConfig, parse_color, parse_idle
from led_catcher.display.clock import COLON_DOTS, IdleScreen, clock_face, clock_texts, dimmed, draw_clock
from led_catcher.display.modes import _resolve_font
from led_catcher.display.worker import DisplayWorker
from led_catcher.handlers.display_api import create_display_router
from led_catcher.profile import DisplayConfig, Profile
from led_catcher.web import EventTracker

TICK = 0.05
TOKEN = "s3cret"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
BLUE = (0, 100, 255)


def local(year, month, day, hour, minute, second) -> float:
    """Epoch seconds for a wall-clock time in this process's timezone."""
    return time.mktime((year, month, day, hour, minute, second, 0, 0, -1))


# Monday 21 September 2026, 14:05.
MONDAY_1405 = local(2026, 9, 21, 14, 5, 0)


class RecordingMatrix:
    """Records draw_text calls: (font name, x, baseline, colour, text)."""

    width = 64
    height = 64

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.calls: list[tuple[str, int, int, tuple, str]] = []
        self.pixels: list[tuple[int, int, tuple]] = []
        self.clears = 0
        self.swaps = 0

    def draw_text(self, font_path, x, y, color, text) -> int:
        with self._lock:
            self.calls.append((font_path.rsplit("/", 1)[-1], x, y, tuple(color), text))
        return 0

    def set_pixel(self, x, y, r, g, b) -> None:
        with self._lock:
            self.pixels.append((x, y, (r, g, b)))

    def show_image(self, image) -> None:
        pass

    def clear(self) -> None:
        with self._lock:
            self.clears += 1

    def swap(self) -> None:
        with self._lock:
            self.swaps += 1

    def texts(self) -> list[str]:
        with self._lock:
            return [call[4] for call in self.calls]


def wait_for(predicate, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False


# ---- the face ------------------------------------------------------------------


def test_the_time_weekday_and_date():
    assert clock_texts(MONDAY_1405) == ("14:05", "Monday", "21.09.2026")
    assert clock_texts(local(2026, 1, 4, 7, 3, 0)) == ("07:03", "Sunday", "04.01.2026"), "zero-padded, English"


def test_the_colon_blinks_every_second():
    assert clock_texts(MONDAY_1405)[0] == "14:05"
    assert clock_texts(MONDAY_1405 + 1)[0] == "14 05"
    assert clock_texts(MONDAY_1405 + 2)[0] == "14:05"


def face(now: float = MONDAY_1405):
    return clock_face(now, 64, 64, _resolve_font("10x20.bdf"), _resolve_font("6x10.bdf"))


def test_the_layout_on_a_64x64_panel():
    lines = [(line.x, line.baseline, line.text, line.dim) for line in face().lines]

    # 20 + 0 + 10 + 1 + 10 rows centred from row 11. Every line centred:
    # "14:05" 50px, "Monday" 36px, "21.09.2026" 60px.
    assert lines == [
        (7, 27, "14 05", False),
        (14, 39, "Monday", True),
        (2, 50, "21 09 2026", True),
    ], "colon and periods are drawn as pixels over spaces"


def test_the_widest_weekday_and_the_date_fit_the_panel():
    wednesday = face(local(2026, 9, 23, 14, 5, 0))

    assert [line.x for line in wednesday.lines[1:]] == [5, 2], "Wednesday 54px, the date 60px"


def test_the_colon_sits_in_the_middle_of_the_digits():
    # The digits' ink spans baseline-13 .. baseline-1; the dots leave three
    # rows above, between and below them.
    colon = sorted({y for x, y, dim in face().dots if y < 30})
    assert colon == [17, 18, 22, 23]
    assert {x for x, y, dim in face().dots if y < 30} == {31, 32, 33}, "the middle of the third cell"


def test_the_periods_are_single_pixels_on_the_digits_bottom_row():
    periods = [(x, y) for x, y, dim in face().dots if y > 40]
    assert periods == [(16, 49), (34, 49)]


def test_without_the_colon_only_the_periods_are_set():
    assert len(face(MONDAY_1405 + 1).dots) == 2
    assert len(face().dots) == len(COLON_DOTS) + 2


def test_the_digits_do_not_move_when_the_colon_blinks():
    assert face().lines == face(MONDAY_1405 + 1).lines


def test_draw_clock_puts_one_frame_on_the_panel():
    matrix = RecordingMatrix()
    draw_clock(matrix, IdleScreen(color=BLUE), MONDAY_1405)

    dim = dimmed(BLUE)
    assert matrix.calls == [
        ("10x20.bdf", 7, 27, BLUE, "14 05"),
        ("6x10.bdf", 14, 39, dim, "Monday"),
        ("6x10.bdf", 2, 50, dim, "21 09 2026"),
    ]
    assert [p for p in matrix.pixels if p[2] == BLUE] == [(x, y, BLUE) for x, y, _ in face().dots[:12]]
    assert [p[:2] for p in matrix.pixels if p[2] == dim] == [(16, 49), (34, 49)]
    assert (matrix.clears, matrix.swaps) == (1, 1)


# ---- the worker ------------------------------------------------------------------


@pytest.fixture
def idle_worker():
    matrix = RecordingMatrix()
    w = DisplayWorker(matrix, idle=IdleScreen(color=BLUE), now=lambda: MONDAY_1405)
    w.start()
    yield w, matrix
    w.stop(timeout=2)


def static(text: str, duration: float = TICK, hold: bool = False) -> DisplayConfig:
    return DisplayConfig(kind="static", text=text, duration=duration, hold=hold)


def test_with_nothing_to_show_the_panel_shows_the_clock(idle_worker):
    w, matrix = idle_worker

    assert wait_for(lambda: "14 05" in matrix.texts())
    assert w.idle_active
    assert w.showing is None, "the idle screen is not a display"


def test_the_clock_redraws_only_when_the_face_changes(idle_worker):
    w, matrix = idle_worker
    assert wait_for(lambda: "14 05" in matrix.texts())

    time.sleep(TICK * 6)

    assert matrix.texts().count("14 05") == 1, "a frozen clock was redrawn"


def test_a_display_interrupts_the_clock_at_once_and_the_clock_comes_back(idle_worker):
    w, matrix = idle_worker
    assert wait_for(lambda: "14 05" in matrix.texts())

    w.submit(static("hello"))
    assert wait_for(lambda: "hello" in matrix.texts(), timeout=0.2), "the clock kept a display waiting"

    assert wait_for(lambda: matrix.texts()[-3:] == ["14 05", "Monday", "21 09 2026"] and w.idle_active), (
        "the clock did not come back after a finite display"
    )


def test_a_held_display_keeps_the_clock_away(idle_worker):
    w, matrix = idle_worker
    assert wait_for(lambda: "14 05" in matrix.texts())

    w.submit(static("7:5", duration=3600, hold=True))
    assert wait_for(lambda: matrix.texts()[-1] == "7:5")
    time.sleep(TICK * 6)

    assert matrix.texts()[-1] == "7:5"
    assert not w.idle_active


def test_blank_returns_to_the_clock(idle_worker):
    w, matrix = idle_worker
    w.submit(static("7:5", duration=3600, hold=True))
    assert wait_for(lambda: matrix.texts()[-1:] == ["7:5"])

    w.blank()

    assert wait_for(lambda: matrix.texts()[-1:] == ["21 09 2026"] and w.idle_active)


def test_switching_the_idle_screen_off_leaves_the_panel_dark(idle_worker):
    w, matrix = idle_worker
    assert wait_for(lambda: w.idle_active)
    clears = matrix.clears

    w.set_idle(None)

    assert wait_for(lambda: not w.idle_active and matrix.clears > clears)
    drawn = len(matrix.texts())
    time.sleep(TICK * 6)
    assert len(matrix.texts()) == drawn, "the clock kept drawing after it was switched off"


def test_switching_it_on_while_idle_starts_the_clock():
    matrix = RecordingMatrix()
    w = DisplayWorker(matrix, now=lambda: MONDAY_1405)
    w.start()
    try:
        time.sleep(TICK * 2)
        assert matrix.texts() == [], "no idle screen, no clock"

        w.set_idle(IdleScreen(color=BLUE))

        assert wait_for(lambda: "14 05" in matrix.texts())
    finally:
        w.stop(timeout=2)


def test_a_clock_that_cannot_draw_switches_itself_off(caplog):
    class Broken(RecordingMatrix):
        def draw_text(self, *args):
            raise OSError("font gone")

    w = DisplayWorker(Broken(), idle=IdleScreen(color=BLUE), now=lambda: MONDAY_1405)
    w.start()
    try:
        assert wait_for(lambda: w.idle is None)
        assert caplog.text.count("idle screen failed") == 1, "retried instead of switched off"
    finally:
        w.stop(timeout=2)


def test_stopping_leaves_the_panel_dark(idle_worker):
    w, matrix = idle_worker
    assert wait_for(lambda: w.idle_active)
    clears = matrix.clears

    w.stop(timeout=2)

    assert matrix.clears > clears
    assert not w.idle_active


# ---- configuration ------------------------------------------------------------


@pytest.mark.parametrize(("raw", "mode"), [("", "off"), ("off", "off"), ("clock", "clock"), (" CLOCK ", "clock")])
def test_led_idle(raw, mode):
    assert parse_idle(raw) == mode


def test_an_unknown_led_idle_fails_startup():
    with pytest.raises(ValueError, match="LED_IDLE 'clok': must be one of off, clock"):
        parse_idle("clok")


@pytest.mark.parametrize(("raw", "color"), [("", "info"), ("Warning", "warning"), ("255, 0,10", (255, 0, 10))])
def test_led_idle_color(raw, color):
    assert parse_color("LED_IDLE_COLOR", raw, "info") == color


@pytest.mark.parametrize("raw", ["1,2", "1,2,3,4", "0,0,256", "a,b,c"])
def test_a_malformed_led_idle_color_fails_startup(raw):
    with pytest.raises(ValueError, match="LED_IDLE_COLOR"):
        parse_color("LED_IDLE_COLOR", raw, "info")


def test_the_idle_colour_name_is_resolved_against_the_profile():
    cfg = Config(redis=RedisConfig(), idle="clock", idle_color="success")
    assert _idle_screen(cfg, Profile()) == (IdleScreen(mode="clock", color=(0, 255, 0)), (0, 255, 0))


def test_an_unknown_idle_colour_name_fails_startup():
    cfg = Config(redis=RedisConfig(), idle="clock", idle_color="teal")
    with pytest.raises(ValueError, match="LED_IDLE_COLOR 'teal'"):
        _idle_screen(cfg, Profile())


def test_with_the_idle_screen_off_the_colour_is_still_resolved():
    # So PUT /display/idle can switch the clock on later without naming one.
    cfg = Config(redis=RedisConfig(), idle="off", idle_color=(1, 2, 3))
    assert _idle_screen(cfg, Profile()) == (None, (1, 2, 3))


# ---- the API --------------------------------------------------------------------


class FakeWorker:
    def __init__(self) -> None:
        self.showing = None
        self.idle = None
        self.idle_active = False

    def set_idle(self, idle) -> None:
        self.idle = idle
        self.idle_active = idle is not None


def make_app(worker=None, token: str = TOKEN, idle=None, tracker=None) -> FastAPI:
    app = FastAPI()
    app.include_router(create_display_router(worker, ApiConfig(token=token), tracker=tracker, idle=idle))
    return app


def client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_get_display_reports_the_idle_screen():
    worker = FakeWorker()
    worker.set_idle(IdleScreen(color=BLUE))
    async with client(make_app(worker, idle=IdleScreen(color=BLUE))) as c:
        body = (await c.get("/display")).json()

    assert body["idle"]["mode"] == "clock"
    assert body["idle"]["color"] == list(BLUE)
    assert body["idle"]["active"] is True
    assert body["idle"]["utcOffset"] == time.localtime().tm_gmtoff


async def test_put_idle_switches_the_clock_on_and_off():
    worker = FakeWorker()
    tracker = EventTracker()
    async with client(make_app(worker, tracker=tracker)) as c:
        on = await c.put("/display/idle", json={"mode": "clock", "color": "success"}, headers=AUTH)
        assert worker.idle == IdleScreen(mode="clock", color=(0, 255, 0))
        off = await c.put("/display/idle", json={"mode": "off"}, headers=AUTH)

    assert on.status_code == 200 and on.json()["idle"]["mode"] == "clock"
    assert off.status_code == 200 and off.json()["idle"]["mode"] == "off"
    assert worker.idle is None
    assert tracker.version == 2, "each switch fires the event stream, so every open canvas follows"


async def test_put_idle_keeps_the_colour_when_none_is_given():
    worker = FakeWorker()
    async with client(make_app(worker)) as c:
        await c.put("/display/idle", json={"mode": "clock", "color": [9, 8, 7]}, headers=AUTH)
        await c.put("/display/idle", json={"mode": "off"}, headers=AUTH)
        await c.put("/display/idle", json={"mode": "clock"}, headers=AUTH)

    assert worker.idle == IdleScreen(mode="clock", color=(9, 8, 7))


@pytest.mark.parametrize(
    ("body", "status"),
    [
        ({"mode": "clock", "color": "teal"}, 400),
        ({"mode": "disco"}, 422),
        ({"mode": "clock", "color": [1, 2, 300]}, 422),
    ],
)
async def test_put_idle_rejects_what_it_cannot_show(body, status):
    worker = FakeWorker()
    async with client(make_app(worker)) as c:
        response = await c.put("/display/idle", json=body, headers=AUTH)

    assert response.status_code == status
    assert worker.idle is None


async def test_put_idle_needs_the_token():
    worker = FakeWorker()
    async with client(make_app(worker)) as c:
        response = await c.put("/display/idle", json={"mode": "clock"})

    assert response.status_code == 401
    assert worker.idle is None


async def test_without_a_token_put_idle_does_not_exist():
    async with client(make_app(FakeWorker(), token="")) as c:
        response = await c.put("/display/idle", json={"mode": "clock"}, headers=AUTH)

    assert response.status_code in (404, 405)


async def test_in_web_mode_the_idle_screen_reaches_the_simulator_only():
    async with client(make_app(None)) as c:
        put = await c.put("/display/idle", json={"mode": "clock"}, headers=AUTH)
        body = (await c.get("/display")).json()

    assert put.json()["panel"] is False
    assert body["idle"]["mode"] == "clock" and body["idle"]["active"] is True
