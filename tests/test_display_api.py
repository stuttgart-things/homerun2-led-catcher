"""The /display API (#80): auth, validation, rate limiting, and what it hands on."""

from __future__ import annotations

import logging

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from led_catcher.config.settings import ApiConfig, load_api_config
from led_catcher.display.worker import Showing
from led_catcher.handlers.display_api import RateLimiter, create_display_router
from led_catcher.profile import DisplayConfig
from led_catcher.web import EventTracker, create_web_app

TOKEN = "s3cret"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class FakeWorker:
    def __init__(self) -> None:
        self.submitted: list[DisplayConfig] = []
        self.blanks = 0
        self.showing: Showing | None = None

    def submit(self, config) -> None:
        self.submitted.append(config)

    def blank(self) -> None:
        self.blanks += 1


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def make_app(worker=None, api: ApiConfig | None = None, tracker=None, colors=None, clock=None) -> FastAPI:
    app = FastAPI()
    kwargs = {"clock": clock} if clock is not None else {}
    app.include_router(
        create_display_router(
            worker, api if api is not None else ApiConfig(token=TOKEN), tracker=tracker, colors=colors, **kwargs
        )
    )
    return app


def client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ---- fail closed -------------------------------------------------------------


async def test_without_a_token_the_write_endpoints_do_not_exist(caplog):
    caplog.set_level(logging.INFO)
    worker = FakeWorker()
    async with client(make_app(worker, ApiConfig(token=""))) as c:
        post = await c.post("/display", json={"kind": "text", "text": "hi"})
        delete = await c.delete("/display")
        get = await c.get("/display")

    assert post.status_code == 405
    assert delete.status_code == 405
    assert get.status_code == 200 and get.json()["writable"] is False
    assert worker.submitted == [] and worker.blanks == 0
    assert "LED_API_TOKEN not set" in caplog.text


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"}, {"Authorization": TOKEN}])
async def test_a_write_without_the_right_token_is_refused(headers):
    worker = FakeWorker()
    async with client(make_app(worker)) as c:
        post = await c.post("/display", json={"kind": "text", "text": "hi"}, headers=headers)
        delete = await c.delete("/display", headers=headers)

    assert post.status_code == 401
    assert delete.status_code == 401
    assert worker.submitted == [] and worker.blanks == 0


async def test_the_bearer_scheme_is_case_insensitive():
    # RFC 7235: the auth scheme is case-insensitive; the token itself is not.
    worker = FakeWorker()
    async with client(make_app(worker)) as c:
        lower = await c.delete("/display", headers={"Authorization": f"bearer {TOKEN}"})
        wrong_case_token = await c.delete("/display", headers={"Authorization": f"Bearer {TOKEN.upper()}"})

    assert lower.status_code == 200
    assert wrong_case_token.status_code == 401


# ---- the happy path ------------------------------------------------------------


async def test_post_hands_a_display_config_to_the_worker():
    worker = FakeWorker()
    body = {"kind": "text", "text": "DEPLOY LÄUFT", "color": [255, 165, 0], "duration": 8, "font": "7x13.bdf"}
    async with client(make_app(worker)) as c:
        res = await c.post("/display", json=body, headers=AUTH)

    assert res.status_code == 200, res.text
    assert res.json()["panel"] is True
    (config,) = worker.submitted
    assert (config.kind, config.text, config.color, config.duration, config.font, config.hold) == (
        "text",
        "DEPLOY LÄUFT",
        (255, 165, 0),
        8.0,
        "7x13.bdf",
        False,
    )


async def test_a_colour_can_be_named_from_the_profile():
    worker = FakeWorker()
    async with client(make_app(worker, colors={"brand": (1, 2, 3)})) as c:
        named = await c.post("/display", json={"kind": "static", "text": "x", "color": "brand"}, headers=AUTH)
        default = await c.post("/display", json={"kind": "static", "text": "x", "color": "WARNING"}, headers=AUTH)
        unknown = await c.post("/display", json={"kind": "static", "text": "x", "color": "mauve"}, headers=AUTH)

    assert named.status_code == 200 and default.status_code == 200
    assert [c.color for c in worker.submitted] == [(1, 2, 3), (255, 165, 0)]
    assert unknown.status_code == 400 and "mauve" in unknown.json()["detail"]


async def test_hold_is_passed_through():
    worker = FakeWorker()
    async with client(make_app(worker)) as c:
        await c.post("/display", json={"kind": "static", "text": "12:04", "hold": True}, headers=AUTH)

    assert worker.submitted[0].hold is True


async def test_an_image_is_resolved_from_visual_aid():
    worker = FakeWorker()
    async with client(make_app(worker)) as c:
        ok = await c.post("/display", json={"kind": "gif", "image": "sunset.gif", "duration": 10}, headers=AUTH)
        missing = await c.post("/display", json={"kind": "gif", "image": "nope.gif"}, headers=AUTH)

    assert ok.status_code == 200, ok.text
    assert worker.submitted[0].image == "sunset.gif"
    assert missing.status_code == 404


async def test_delete_blanks_the_panel():
    worker = FakeWorker()
    async with client(make_app(worker)) as c:
        res = await c.delete("/display", headers=AUTH)

    assert res.status_code == 200
    assert worker.blanks == 1


async def test_without_a_panel_writes_reach_only_the_simulator():
    tracker = EventTracker()
    async with client(make_app(None, tracker=tracker)) as c:
        res = await c.post("/display", json={"kind": "text", "text": "hi"}, headers=AUTH)

    assert res.status_code == 200
    assert res.json()["panel"] is False
    assert tracker.recent(1)[0].title == "hi"


# ---- validation ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({"kind": "text"}, 400),  # a text kind needs text
        ({"kind": "static", "text": "   "}, 400),
        ({"kind": "image"}, 400),  # an image kind needs an image
        ({"kind": "blink", "text": "x"}, 422),
        ({"kind": "text", "text": "x", "severity": "error"}, 422),  # no rule-matching fields
        ({"kind": "text", "text": "x", "systems": ["a"]}, 422),
        ({"kind": "text", "text": "x", "duration": 0}, 422),
        ({"kind": "text", "text": "x", "duration": 601}, 422),
        ({"kind": "text", "text": "x", "color": [256, 0, 0]}, 422),
        ({"kind": "text", "text": "x", "color": [1, 2]}, 422),
        ({"kind": "text", "text": "x", "font": "../../etc/passwd"}, 422),
        ({"kind": "image", "image": "/etc/passwd"}, 422),
        ({"kind": "image", "image": "sub/dir.png"}, 422),
    ],
)
async def test_invalid_requests_are_rejected_and_never_reach_the_panel(body, code):
    worker = FakeWorker()
    async with client(make_app(worker)) as c:
        res = await c.post("/display", json=body, headers=AUTH)

    assert res.status_code == code, res.text
    assert worker.submitted == []


async def test_text_is_capped():
    worker = FakeWorker()
    async with client(make_app(worker, ApiConfig(token=TOKEN, max_text=5))) as c:
        ok = await c.post("/display", json={"kind": "text", "text": "12345"}, headers=AUTH)
        long = await c.post("/display", json={"kind": "text", "text": "123456"}, headers=AUTH)

    assert ok.status_code == 200
    assert long.status_code == 400
    assert len(worker.submitted) == 1


# ---- rate limit ----------------------------------------------------------------


async def test_writes_are_rate_limited_across_callers():
    worker = FakeWorker()
    clock = FakeClock()
    async with client(make_app(worker, ApiConfig(token=TOKEN, rate_limit=3), clock=clock)) as c:
        codes = [
            (await c.post("/display", json={"kind": "text", "text": f"m{i}"}, headers=AUTH)).status_code
            for i in range(3)
        ]
        limited = await c.delete("/display", headers=AUTH)
        clock.now += 60
        again = await c.post("/display", json={"kind": "text", "text": "later"}, headers=AUTH)

    assert codes == [200, 200, 200]
    assert limited.status_code == 429
    assert int(limited.headers["Retry-After"]) >= 1
    assert worker.blanks == 0
    assert again.status_code == 200


def test_rate_limiter_window_slides():
    clock = FakeClock()
    limiter = RateLimiter(2, window=10, clock=clock)
    assert limiter.acquire() == 0
    clock.now += 5
    assert limiter.acquire() == 0
    assert limiter.acquire() == pytest.approx(5)
    clock.now += 5
    assert limiter.acquire() == 0  # the first hit has left the window


async def test_a_bad_token_does_not_spend_the_rate_limit():
    worker = FakeWorker()
    async with client(make_app(worker, ApiConfig(token=TOKEN, rate_limit=1), clock=FakeClock())) as c:
        for _ in range(5):
            await c.post("/display", json={"kind": "text", "text": "x"}, headers={"Authorization": "Bearer no"})
        res = await c.post("/display", json={"kind": "text", "text": "x"}, headers=AUTH)

    assert res.status_code == 200


# ---- GET /display and /display/options -----------------------------------------


async def test_get_reports_what_is_showing():
    worker = FakeWorker()
    async with client(make_app(worker)) as c:
        dark = (await c.get("/display")).json()
        worker.showing = Showing(DisplayConfig(kind="static", text="12:04", hold=True, color=(1, 2, 3)), 0.0)
        lit = (await c.get("/display")).json()

    assert dark["showing"] is False and dark["display"] is None
    assert lit["showing"] is True and lit["held"] is True
    assert lit["display"]["text"] == "12:04"
    assert lit["display"]["color"] == [1, 2, 3]
    assert lit["since"].startswith("1970-01-01")


async def test_options_list_the_shipped_assets():
    async with client(make_app(FakeWorker())) as c:
        options = (await c.get("/display/options")).json()

    assert "sunset.gif" in options["images"]
    assert "6x10.bdf" in options["fonts"]
    assert set(options["kinds"]) == {"static", "text", "ticker", "image", "gif", "score"}
    assert options["colors"]["error"] == [255, 0, 0]


# ---- the simulator sees what the panel sees ------------------------------------


async def test_writes_are_recorded_for_the_simulator():
    tracker = EventTracker()
    async with client(make_app(FakeWorker(), tracker=tracker)) as c:
        await c.post(
            "/display", json={"kind": "static", "text": "12:04", "hold": True, "color": [9, 9, 9]}, headers=AUTH
        )
        await c.delete("/display", headers=AUTH)

    cleared, shown = tracker.recent(2)
    assert (shown.kind, shown.title, shown.color, shown.hold) == ("static", "12:04", (9, 9, 9), True)
    assert shown.system == "api"
    assert cleared.kind == "clear"


async def test_the_timeline_escapes_api_text():
    tracker = EventTracker()
    app = create_web_app(tracker)
    app.include_router(create_display_router(FakeWorker(), ApiConfig(token=TOKEN), tracker=tracker))
    async with client(app) as c:
        await c.post("/display", json={"kind": "text", "text": "<img src=x onerror=alert(1)>"}, headers=AUTH)
        events = (await c.get("/events")).text

    assert "<img" not in events
    assert "&lt;img" in events


@pytest.mark.parametrize("display_api", [True, False])
async def test_the_control_panel_is_shown_only_when_writes_are_possible(display_api):
    app = create_web_app(EventTracker(), mode="standalone", display_api=display_api)
    async with client(app) as c:
        page = (await c.get("/")).text

    assert ('id="panel-control"' in page) is display_api
    assert "<strong>standalone</strong>" in page
    assert "{{" not in page


# ---- config ----------------------------------------------------------------------


def test_api_config_from_env(monkeypatch):
    monkeypatch.setenv("LED_API_TOKEN", "  tok  ")
    monkeypatch.setenv("LED_API_MAX_TEXT", "64")
    monkeypatch.setenv("LED_API_RATE_LIMIT", "10")
    api = load_api_config()
    assert (api.token, api.max_text, api.rate_limit, api.writable) == ("tok", 64, 10, True)


def test_api_config_defaults(monkeypatch):
    for key in ("LED_API_TOKEN", "LED_API_MAX_TEXT", "LED_API_RATE_LIMIT"):
        monkeypatch.delenv(key, raising=False)
    api = load_api_config()
    assert (api.token, api.max_text, api.rate_limit, api.writable) == ("", 256, 30, False)


@pytest.mark.parametrize(("key", "value"), [("LED_API_MAX_TEXT", "0"), ("LED_API_RATE_LIMIT", "lots")])
def test_api_config_rejects_bad_values(monkeypatch, key, value):
    monkeypatch.setenv(key, value)
    with pytest.raises(ValueError, match=key):
        load_api_config()
