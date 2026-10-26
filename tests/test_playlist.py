"""Playlists (#146): the schedule, the worker playing it, and the API that starts it."""

from __future__ import annotations

import threading
import time

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from PIL import Image

from led_catcher.config.settings import ApiConfig
from led_catcher.display.frames import clear_animation_cache
from led_catcher.display.playlist import Playlist
from led_catcher.display.worker import DisplayWorker
from led_catcher.handlers import display_api
from led_catcher.handlers.display_api import create_display_router
from led_catcher.profile import DisplayConfig
from led_catcher.web import EventTracker

TOKEN = "s3cret"
AUTH = {"Authorization": f"Bearer {TOKEN}"}

COLOURS = {"red.gif": (255, 0, 0), "green.gif": (0, 255, 0), "blue.png": (0, 0, 255)}


# ---- the schedule ------------------------------------------------------------


def test_a_sequence_plays_each_image_for_its_duration_round_and_round():
    p = Playlist(images=("a", "b", "c"), duration=10, started=100.0)

    assert [p.slot(100.0 + t).image for t in (0, 9.9, 10, 25, 30, 61)] == ["a", "a", "b", "c", "a", "a"]
    slot = p.slot(125.0)
    assert (slot.position, slot.start, slot.end) == (2, 120.0, 130.0)


def test_a_total_ends_the_playlist_and_cuts_its_last_slot():
    p = Playlist(images=("a", "b"), duration=10, total=25, started=0.0)

    assert p.until == 25
    assert p.slot(24.0).end == 25
    assert p.slot(25.0) is None and p.expired(25.0)


def test_without_a_total_the_playlist_does_not_end():
    p = Playlist(images=("a",), duration=5, started=0.0)

    assert p.until is None and not p.expired(10**9) and p.slot(10**9).image == "a"


def test_random_plays_every_image_once_a_round_and_never_one_twice_in_a_row():
    images = tuple("abcdef")
    p = Playlist(images=images, duration=1, order="random", started=0.0, seed=7)

    played = [p.image_at(n) for n in range(len(images) * 40)]
    for r in range(40):
        assert sorted(played[r * 6 : r * 6 + 6]) == sorted(images)
    assert all(a != b for a, b in zip(played, played[1:], strict=False))
    assert played[:6] != list(images) or played[6:12] != list(images)


def test_the_shuffle_is_the_same_for_every_reader_of_one_playlist():
    a = Playlist(images=tuple("abcde"), duration=1, order="random", seed=42)
    b = Playlist(images=tuple("abcde"), duration=1, order="random", seed=42)

    assert [a.image_at(n) for n in range(50)] == [b.image_at(n) for n in range(50)]


def test_random_with_two_images_alternates():
    p = Playlist(images=("a", "b"), duration=1, order="random")

    assert [p.image_at(n) for n in range(4)] == ["a", "b", "a", "b"]


@pytest.mark.parametrize(
    "kwargs", [{"images": ()}, {"images": ("a",), "duration": 0}, {"images": ("a",), "order": "backwards"}]
)
def test_a_playlist_rejects_what_it_cannot_play(kwargs):
    with pytest.raises(ValueError):
        Playlist(**{"duration": 1, **kwargs})


# ---- the worker ----------------------------------------------------------------


class RecordingDisplay:
    """A panel that remembers the colour of every image put up on it."""

    width = 64
    height = 64

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.colours: list[tuple[int, int, int]] = []
        self.texts: list[str] = []

    def show_image(self, image) -> None:
        with self._lock:
            colour = image.getpixel((32, 32))
            if not self.colours or self.colours[-1] != colour:
                self.colours.append(colour)

    def draw_text(self, font_path, x, y, color, text) -> int:
        with self._lock:
            self.texts.append(text)
        return len(text) * 6

    def clear(self) -> None:
        pass

    def swap(self) -> None:
        pass

    def seen(self) -> list[tuple[int, int, int]]:
        with self._lock:
            return list(self.colours)


@pytest.fixture
def visual_aid(tmp_path, monkeypatch):
    """red.gif and green.gif (two frames each, so they animate) and blue.png."""
    for name, colour in COLOURS.items():
        frames = [Image.new("RGB", (64, 64), colour), Image.new("RGB", (64, 64), colour)]
        if name.endswith(".gif"):
            frames[0].save(tmp_path / name, save_all=True, append_images=frames[1:], duration=20, loop=0)
        else:
            frames[0].save(tmp_path / name)
    monkeypatch.setenv("VISUAL_AID_DIR", str(tmp_path))
    monkeypatch.setenv("LED_REMOTE_CACHE_DIR", str(tmp_path / "remote"))
    clear_animation_cache()
    yield tmp_path
    clear_animation_cache()


@pytest.fixture
def panel(visual_aid):
    display = RecordingDisplay()
    w = DisplayWorker(display)
    w.start()
    yield w, display
    w.stop()


def wait_for(predicate, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def playlist(*images, duration=0.3, **kw) -> Playlist:
    return Playlist(images=images, duration=duration, started=time.time(), **kw)


def test_the_worker_plays_the_playlist_in_order(panel):
    worker, display = panel
    worker.set_playlist(playlist("red.gif", "green.gif", "blue.png"))

    assert wait_for(lambda: len(display.seen()) >= 4)
    assert display.seen()[:4] == [COLOURS["red.gif"], COLOURS["green.gif"], COLOURS["blue.png"], COLOURS["red.gif"]]
    assert worker.playlist_active


def test_a_message_interrupts_the_playlist_and_it_plays_on_afterwards(panel):
    worker, display = panel
    worker.set_playlist(playlist("red.gif", duration=5))
    assert wait_for(lambda: display.seen() == [COLOURS["red.gif"]])

    worker.submit(DisplayConfig(kind="static", text="MSG", duration=0.1))

    assert wait_for(lambda: "MSG" in display.texts)
    # Back to the playlist once the message is done, within the same slot.
    assert wait_for(lambda: display.seen().count(COLOURS["red.gif"]) == 1 and worker.playlist_active)
    assert worker.playlist is not None


def test_a_playlist_ends_a_held_display(panel):
    worker, display = panel
    worker.submit(DisplayConfig(kind="static", text="HELD", hold=True))
    assert wait_for(lambda: worker.showing is not None and worker.showing.held)

    worker.set_playlist(playlist("green.gif", duration=5))

    assert wait_for(lambda: display.seen() == [COLOURS["green.gif"]])
    assert worker.showing is None


def test_a_playlist_with_a_total_ends_and_is_dropped(panel):
    worker, display = panel
    worker.set_playlist(playlist("red.gif", "green.gif", duration=0.3, total=0.5))

    assert wait_for(lambda: worker.playlist is None and not worker.playlist_active)
    assert COLOURS["red.gif"] in display.seen()


def test_stopping_and_blank_end_the_playlist(panel):
    worker, display = panel
    worker.set_playlist(playlist("red.gif", duration=5))
    assert wait_for(lambda: worker.playlist_active)

    worker.set_playlist(None)
    assert wait_for(lambda: not worker.playlist_active)

    worker.set_playlist(playlist("red.gif", duration=5))
    assert wait_for(lambda: worker.playlist_active)
    worker.blank()
    assert wait_for(lambda: not worker.playlist_active)
    assert worker.playlist is None


def test_a_missing_image_does_not_stop_the_playlist(panel):
    worker, display = panel
    worker.set_playlist(playlist("gone.gif", "green.gif", duration=0.3))

    assert wait_for(lambda: COLOURS["green.gif"] in display.seen())


# ---- the API ---------------------------------------------------------------------


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
        self.submitted: list[DisplayConfig] = []
        self.blanks = 0

    def set_playlist(self, playlist) -> None:
        self.playlist = playlist
        self.playlist_active = playlist is not None

    def submit(self, config, system: str = "", title: str = "") -> None:
        self.submitted.append(config)

    def blank(self) -> None:
        self.blanks += 1
        self.playlist = None


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def make_app(worker=None, tracker=None, now=None) -> FastAPI:
    app = FastAPI()
    kwargs = {"now": now} if now is not None else {}
    app.include_router(create_display_router(worker, ApiConfig(token=TOKEN), tracker=tracker, **kwargs))
    return app


def client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_put_playlist_hands_it_to_the_worker_and_get_display_reports_it(visual_aid):
    worker, tracker, clock = FakeWorker(), EventTracker(), Clock()
    async with client(make_app(worker, tracker, clock)) as c:
        res = await c.put(
            "/display/playlist",
            headers=AUTH,
            json={"images": ["red.gif", "green.gif"], "order": "sequence", "duration": 10, "total": 600},
        )
        clock.now += 15
        body = (await c.get("/display")).json()

    assert res.status_code == 200, res.text
    assert worker.playlist.images == ("red.gif", "green.gif")
    assert worker.playlist.total == 600 and worker.playlist.started == 1000.0
    assert tracker.version == 1
    p = body["playlist"]
    assert p["active"] is True and p["until"] == 1600.0 and p["now"] == 1015.0
    assert p["current"] == {"position": 1, "image": "green.gif", "start": 1010.0, "end": 1020.0}


async def test_a_playlist_in_web_mode_is_reported_without_a_worker(visual_aid):
    clock = Clock()
    async with client(make_app(None, now=clock)) as c:
        await c.put("/display/playlist", headers=AUTH, json={"images": ["blue.png"], "duration": 5, "total": 10})
        during = (await c.get("/display")).json()["playlist"]
        clock.now += 10
        after = (await c.get("/display")).json()["playlist"]

    assert during["current"]["image"] == "blue.png"
    assert after is None


async def test_delete_playlist_and_delete_display_stop_it(visual_aid):
    worker = FakeWorker()
    async with client(make_app(worker)) as c:
        await c.put("/display/playlist", headers=AUTH, json={"images": ["red.gif"]})
        stop = await c.delete("/display/playlist", headers=AUTH)
        stopped = (await c.get("/display")).json()["playlist"]
        await c.put("/display/playlist", headers=AUTH, json={"images": ["red.gif"]})
        await c.delete("/display", headers=AUTH)
        blanked = (await c.get("/display")).json()["playlist"]

    assert stop.status_code == 200 and stopped is None
    assert blanked is None and worker.blanks == 1


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({"images": []}, 422),
        ({"images": ["../etc/passwd"]}, 422),
        ({"images": ["http://example.com/a.gif"]}, 422),
        ({"images": ["red.gif"], "order": "backwards"}, 422),
        ({"images": ["red.gif"], "duration": 0}, 422),
        ({"images": ["red.gif"] * 51}, 422),
        ({"images": ["nope.gif"]}, 404),
    ],
)
async def test_put_playlist_rejects_what_it_cannot_play(visual_aid, body, code):
    worker = FakeWorker()
    async with client(make_app(worker)) as c:
        res = await c.put("/display/playlist", headers=AUTH, json=body)

    assert res.status_code == code, res.text
    assert worker.playlist is None


async def test_the_playlist_endpoints_need_the_token(visual_aid):
    async with client(make_app(FakeWorker())) as c:
        put = await c.put("/display/playlist", json={"images": ["red.gif"]})
        delete = await c.delete("/display/playlist")

    assert put.status_code == 401 and delete.status_code == 401


async def test_urls_in_a_playlist_and_a_display_are_fetched_and_named_by_their_cached_file(visual_aid, monkeypatch):
    fetched = []

    def fake_fetch(url):
        fetched.append(url)
        (visual_aid / "url-cached.gif").write_bytes((visual_aid / "red.gif").read_bytes())
        return "url-cached.gif"

    monkeypatch.setattr(display_api, "fetch_image", fake_fetch)
    worker = FakeWorker()
    async with client(make_app(worker)) as c:
        pl = await c.put("/display/playlist", headers=AUTH, json={"images": ["red.gif", "https://example.com/cat.gif"]})
        one = await c.post("/display", headers=AUTH, json={"kind": "gif", "image": "https://example.com/cat.gif"})

    assert pl.status_code == 200 and one.status_code == 200, (pl.text, one.text)
    assert fetched == ["https://example.com/cat.gif"] * 2
    assert worker.playlist.images == ("red.gif", "url-cached.gif")
    assert worker.submitted[0].image == "url-cached.gif"


async def test_a_url_that_cannot_be_fetched_is_answered_with_its_status(visual_aid, monkeypatch):
    from led_catcher.display.remote import RemoteImageError

    def failing_fetch(url):
        raise RemoteImageError("HTTP 404", status=502)

    monkeypatch.setattr(display_api, "fetch_image", failing_fetch)
    worker = FakeWorker()
    async with client(make_app(worker)) as c:
        res = await c.post("/display", headers=AUTH, json={"kind": "gif", "image": "https://example.com/x.gif"})

    assert res.status_code == 502 and worker.submitted == []


async def test_options_name_the_playlist_orders(visual_aid):
    async with client(make_app(FakeWorker())) as c:
        o = (await c.get("/display/options")).json()

    assert o["playlistOrders"] == ["sequence", "random"] and o["maxPlaylistItems"] == 50
    assert "red.gif" in o["images"]
