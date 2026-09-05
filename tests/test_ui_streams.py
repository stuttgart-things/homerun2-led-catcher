"""Tests for the stream control in the HTMX web simulator."""

from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from led_catcher.config.settings import Config, RedisConfig, parse_stream_presets
from led_catcher.consumer.redis_consumer import RedisConsumer
from led_catcher.web import EventTracker, LedEvent, create_web_app
from led_catcher.web.app import _render_streams_control, event_stream


class FakeRedis:
    """Enough of a Redis to let set_streams() run without a server."""

    def __init__(self) -> None:
        self.setids: list[tuple[str, str, str]] = []

    async def xinfo_groups(self, stream: str):
        return [{"name": "homerun2-led-catcher"}]

    async def xgroup_create(self, stream: str, group: str, id: str = "0", mkstream: bool = False):
        pass

    async def xgroup_setid(self, stream: str, group: str, id: str):
        self.setids.append((stream, group, id))


def make_consumer(streams: list[str]) -> RedisConsumer:
    cfg = Config(redis=RedisConfig(streams=streams), consumer_name="test-consumer")
    consumer = RedisConsumer(cfg, handlers=[])
    consumer._client = FakeRedis()  # type: ignore[assignment]
    return consumer


def make_app(consumer: RedisConsumer, presets: list[list[str]]):
    return create_web_app(EventTracker(), consumer=consumer, presets=presets)


def client_for(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# --- preset parsing -------------------------------------------------------


def test_presets_comma_separates_buttons():
    assert parse_stream_presets("messages,tabletennis", ["messages"]) == [
        ["messages"],
        ["tabletennis"],
    ]


def test_presets_pipe_groups_streams_into_one_button():
    assert parse_stream_presets("messages,tabletennis|scale", ["messages"]) == [
        ["messages"],
        ["tabletennis", "scale"],
    ]


def test_presets_trim_and_drop_empty_entries():
    assert parse_stream_presets("  messages  , , tabletennis |  ", ["x"]) == [
        ["messages"],
        ["tabletennis"],
    ]


def test_presets_collapse_duplicates():
    assert parse_stream_presets("messages,messages", ["x"]) == [["messages"]]


def test_presets_fall_back_to_configured_streams():
    assert parse_stream_presets("", ["messages", "scale"]) == [["messages", "scale"]]


def test_presets_fall_back_to_empty_when_nothing_configured():
    assert parse_stream_presets("", []) == []


# --- configured baseline --------------------------------------------------


@pytest.mark.asyncio
async def test_set_streams_does_not_mutate_the_configured_baseline():
    configured = ["messages"]
    cfg = Config(redis=RedisConfig(streams=configured))
    consumer = RedisConsumer(cfg, handlers=[])
    consumer._client = FakeRedis()  # type: ignore[assignment]

    await consumer.set_streams(["tabletennis"])

    assert consumer.streams == ["tabletennis"]
    assert consumer.configured_streams == ["messages"]
    # The Config object itself is the origin of the baseline and stays untouched.
    assert cfg.redis.streams == ["messages"]
    assert configured == ["messages"]


# --- override indicator ---------------------------------------------------


@pytest.mark.asyncio
async def test_control_shows_no_override_in_normal_state():
    consumer = make_consumer(["messages"])
    html = _render_streams_control(consumer, [["messages"], ["tabletennis"]])

    assert "messages" in html
    assert "overridden" not in html
    assert "stream-reset" not in html
    assert consumer.is_overridden is False


@pytest.mark.asyncio
async def test_control_marks_override_and_offers_reset():
    consumer = make_consumer(["messages"])
    await consumer.set_streams(["tabletennis"])

    html = _render_streams_control(consumer, [["messages"], ["tabletennis"]])

    assert consumer.is_overridden is True
    assert "overridden" in html
    assert "stream-reset" in html
    # The reset action posts the configured set back.
    assert "messages" in html


@pytest.mark.asyncio
async def test_control_marks_the_active_preset():
    consumer = make_consumer(["messages"])
    html = _render_streams_control(consumer, [["messages"], ["tabletennis"]])
    assert 'class="stream-preset active"' in html
    assert html.count('class="stream-preset"') == 1


@pytest.mark.asyncio
async def test_control_offers_a_way_back_when_config_is_not_a_preset():
    consumer = make_consumer(["messages"])
    await consumer.set_streams(["tabletennis"])

    html = _render_streams_control(consumer, [["tabletennis"]])

    # No preset would restore "messages", so the control adds one.
    assert html.count("messages") >= 1
    assert "stream-reset" in html


def test_control_renders_nothing_without_a_consumer():
    assert _render_streams_control(None, [["messages"]]) == ""


@pytest.mark.asyncio
async def test_control_escapes_stream_names():
    consumer = make_consumer(["messages"])
    await consumer.set_streams(["<img src=x onerror=alert(1)>"])

    html = _render_streams_control(consumer, [["messages"]])

    assert "<img src=x" not in html
    assert "&lt;img src=x" in html


# --- /ui/streams route ----------------------------------------------------


@pytest.mark.asyncio
async def test_ui_streams_get_returns_the_partial():
    consumer = make_consumer(["messages"])
    async with client_for(make_app(consumer, [["messages"]])) as client:
        resp = await client.get("/ui/streams")

    assert resp.status_code == 200
    assert "messages" in resp.text


@pytest.mark.asyncio
async def test_ui_streams_post_switches_and_returns_the_partial():
    consumer = make_consumer(["messages"])
    async with client_for(make_app(consumer, [["messages"], ["tabletennis"]])) as client:
        resp = await client.post("/ui/streams", data={"streams": "tabletennis"})

    assert resp.status_code == 200
    assert consumer.streams == ["tabletennis"]
    assert "overridden" in resp.text


@pytest.mark.asyncio
async def test_ui_streams_post_accepts_a_pipe_separated_group():
    consumer = make_consumer(["messages"])
    async with client_for(make_app(consumer, [["messages"]])) as client:
        resp = await client.post("/ui/streams", data={"streams": "tabletennis|scale"})

    assert resp.status_code == 200
    assert consumer.streams == ["tabletennis", "scale"]


@pytest.mark.asyncio
async def test_ui_streams_post_reports_an_invalid_set_without_switching():
    consumer = make_consumer(["messages"])
    async with client_for(make_app(consumer, [["messages"]])) as client:
        resp = await client.post("/ui/streams", data={"streams": "  |  "})

    # 200 with the error in the partial: htmx does not swap 4xx by default, and the
    # control has to say why nothing happened.
    assert resp.status_code == 200
    assert "stream-error" in resp.text
    assert consumer.streams == ["messages"]


@pytest.mark.asyncio
async def test_ui_streams_post_requires_the_field():
    consumer = make_consumer(["messages"])
    async with client_for(make_app(consumer, [["messages"]])) as client:
        resp = await client.post("/ui/streams", data={})

    assert resp.status_code == 422
    assert consumer.streams == ["messages"]


@pytest.mark.asyncio
async def test_ui_streams_routes_absent_without_a_consumer():
    app = create_web_app(EventTracker())
    async with client_for(app) as client:
        assert (await client.get("/ui/streams")).status_code == 404
        assert (await client.post("/ui/streams", data={"streams": "x"})).status_code == 404


# --- index page -----------------------------------------------------------


@pytest.mark.asyncio
async def test_index_renders_the_control():
    consumer = make_consumer(["messages"])
    async with client_for(make_app(consumer, [["messages"], ["tabletennis"]])) as client:
        resp = await client.get("/")

    assert resp.status_code == 200
    assert 'id="streams-control"' in resp.text
    assert 'sse-swap="streams-update"' in resp.text
    assert "tabletennis" in resp.text
    assert "{{ streams_control }}" not in resp.text


@pytest.mark.asyncio
async def test_index_leaves_the_control_empty_without_a_consumer():
    app = create_web_app(EventTracker())
    async with client_for(app) as client:
        resp = await client.get("/")

    assert resp.status_code == 200
    assert "{{ streams_control }}" not in resp.text
    # The .stream-* CSS is always in the template; what must be absent is the control.
    assert 'hx-post="/ui/streams"' not in resp.text
    assert "Streams:" not in resp.text


# --- SSE streams-update ---------------------------------------------------


async def _drain(gen, limit: int = 20) -> list[dict]:
    """Collect events until the generator stops or `limit` is reached."""
    out: list[dict] = []
    async for event in gen:
        out.append(event)
        if len(out) >= limit:
            break
    return out


def _never_disconnected():
    async def _f() -> bool:
        return False

    return _f


def _disconnect_after(n: int):
    """is_disconnected() that reports the client gone after n polls."""
    state = {"calls": 0}

    async def _f() -> bool:
        state["calls"] += 1
        return state["calls"] > n

    return _f


@pytest.mark.asyncio
async def test_sse_emits_streams_update_on_connect():
    consumer = make_consumer(["messages"])
    gen = event_stream(EventTracker(), _disconnect_after(1), consumer, [["messages"]], interval=0)

    events = await asyncio.wait_for(_drain(gen), timeout=5)

    assert [e["event"] for e in events] == ["streams-update"]
    assert "messages" in events[0]["data"]
    assert "overridden" not in events[0]["data"]


@pytest.mark.asyncio
async def test_sse_emits_streams_update_when_another_client_switches():
    consumer = make_consumer(["messages"])
    gen = event_stream(EventTracker(), _disconnect_after(3), consumer, [["messages"], ["tabletennis"]], interval=0)

    # First poll reports the initial set.
    first = await gen.__anext__()
    assert first["event"] == "streams-update"
    assert "overridden" not in first["data"]

    # Switch behind the UI's back, the way the JSON API or the zaehlwerk-api would.
    await consumer.set_streams(["tabletennis"])

    rest = await asyncio.wait_for(_drain(gen), timeout=5)
    assert [e["event"] for e in rest] == ["streams-update"]
    assert "tabletennis" in rest[0]["data"]
    assert "overridden" in rest[0]["data"]


@pytest.mark.asyncio
async def test_sse_stays_quiet_while_the_set_is_unchanged():
    consumer = make_consumer(["messages"])
    gen = event_stream(EventTracker(), _disconnect_after(5), consumer, [["messages"]], interval=0)

    events = await asyncio.wait_for(_drain(gen), timeout=5)

    # One event on connect, nothing after — no polling loop, no repeats.
    assert len(events) == 1


@pytest.mark.asyncio
async def test_sse_emits_no_streams_update_without_a_consumer():
    gen = event_stream(EventTracker(), _disconnect_after(3), None, [], interval=0)

    events = await asyncio.wait_for(_drain(gen), timeout=5)

    assert events == []


@pytest.mark.asyncio
async def test_sse_still_emits_event_and_stats_updates():
    tracker = EventTracker()
    consumer = make_consumer(["messages"])
    tracker.record(LedEvent(timestamp="12:00:00", severity="info", system="flux", title="OK"))
    gen = event_stream(tracker, _disconnect_after(1), consumer, [["messages"]], interval=0)

    events = await asyncio.wait_for(_drain(gen), timeout=5)

    assert [e["event"] for e in events] == ["events-update", "stats-update", "streams-update"]
