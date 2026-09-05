"""Tests for runtime stream switching and the /streams control endpoints."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from led_catcher.config.settings import Config, RedisConfig
from led_catcher.consumer.redis_consumer import RedisConsumer, normalize_streams
from led_catcher.handlers.control import create_control_router


class FakeRedis:
    """Records the group-management calls set_streams makes."""

    def __init__(self, existing_groups: dict[str, list[str]] | None = None) -> None:
        self._groups = existing_groups or {}
        self.created: list[tuple[str, str]] = []
        self.setids: list[tuple[str, str, str]] = []

    async def xinfo_groups(self, stream: str):
        return [{"name": g} for g in self._groups.get(stream, [])]

    async def xgroup_create(self, stream: str, group: str, id: str = "0", mkstream: bool = False):
        self._groups.setdefault(stream, []).append(group)
        self.created.append((stream, group))

    async def xgroup_setid(self, stream: str, group: str, id: str):
        self.setids.append((stream, group, id))


def make_consumer(streams: list[str], fake: FakeRedis) -> RedisConsumer:
    cfg = Config(redis=RedisConfig(streams=streams), consumer_name="test-consumer")
    consumer = RedisConsumer(cfg, handlers=[])
    consumer._client = fake  # type: ignore[assignment]
    return consumer


def test_normalize_streams_trims_and_dedupes():
    assert normalize_streams([" messages ", "scale", "", "  ", "messages"]) == ["messages", "scale"]


def test_consumer_starts_from_config_streams():
    consumer = make_consumer(["messages", "scale"], FakeRedis())
    assert consumer.streams == ["messages", "scale"]


def test_streams_property_returns_a_copy():
    consumer = make_consumer(["messages"], FakeRedis())
    consumer.streams.append("tampered")
    assert consumer.streams == ["messages"]


@pytest.mark.asyncio
async def test_set_streams_creates_group_on_added_stream():
    fake = FakeRedis(existing_groups={"messages": ["homerun2-led-catcher"]})
    consumer = make_consumer(["messages"], fake)

    result = await consumer.set_streams(["tabletennis"])

    assert consumer.streams == ["tabletennis"]
    assert result["added"] == ["tabletennis"]
    assert result["removed"] == ["messages"]
    assert fake.created == [("tabletennis", "homerun2-led-catcher")]


@pytest.mark.asyncio
async def test_set_streams_skips_backlog_on_added_stream():
    fake = FakeRedis(existing_groups={"messages": ["homerun2-led-catcher"]})
    consumer = make_consumer(["messages"], fake)

    await consumer.set_streams(["tabletennis"])

    assert fake.setids == [("tabletennis", "homerun2-led-catcher", "$")]


@pytest.mark.asyncio
async def test_switching_back_skips_the_backlog_that_accumulated():
    fake = FakeRedis(existing_groups={"messages": ["homerun2-led-catcher"]})
    consumer = make_consumer(["messages"], fake)

    await consumer.set_streams(["tabletennis"])
    await consumer.set_streams(["messages"])

    assert consumer.streams == ["messages"]
    assert fake.setids == [
        ("tabletennis", "homerun2-led-catcher", "$"),
        ("messages", "homerun2-led-catcher", "$"),
    ]
    # The group already existed on messages, so it is not recreated.
    assert ("messages", "homerun2-led-catcher") not in fake.created


@pytest.mark.asyncio
async def test_skip_backlog_false_keeps_the_backlog():
    fake = FakeRedis(existing_groups={"messages": ["homerun2-led-catcher"]})
    consumer = make_consumer(["messages"], fake)

    result = await consumer.set_streams(["tabletennis"], skip_backlog=False)

    assert fake.setids == []
    assert result["skipBacklog"] is False


@pytest.mark.asyncio
async def test_existing_streams_are_left_untouched_when_adding():
    fake = FakeRedis(existing_groups={"messages": ["homerun2-led-catcher"]})
    consumer = make_consumer(["messages"], fake)

    result = await consumer.set_streams(["messages", "scale"])

    assert consumer.streams == ["messages", "scale"]
    assert result["added"] == ["scale"]
    assert result["removed"] == []
    # Only the added stream is touched — messages keeps its last-delivered-id.
    assert fake.setids == [("scale", "homerun2-led-catcher", "$")]
    assert fake.created == [("scale", "homerun2-led-catcher")]


@pytest.mark.asyncio
async def test_set_streams_trims_input():
    fake = FakeRedis(existing_groups={"messages": ["homerun2-led-catcher"]})
    consumer = make_consumer(["messages"], fake)

    await consumer.set_streams(["  tabletennis  ", "", "tabletennis"])

    assert consumer.streams == ["tabletennis"]


@pytest.mark.asyncio
async def test_set_streams_rejects_an_empty_list():
    consumer = make_consumer(["messages"], FakeRedis())

    with pytest.raises(ValueError):
        await consumer.set_streams([])

    with pytest.raises(ValueError):
        await consumer.set_streams(["   "])

    assert consumer.streams == ["messages"]


def build_client_app(consumer: RedisConsumer) -> FastAPI:
    app = FastAPI()
    app.include_router(create_control_router(consumer))
    return app


@pytest.mark.asyncio
async def test_get_streams_route():
    consumer = make_consumer(["messages"], FakeRedis())
    transport = ASGITransport(app=build_client_app(consumer))

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/streams")

    assert resp.status_code == 200
    data = resp.json()
    assert data["streams"] == ["messages"]
    assert data["consumerGroup"] == "homerun2-led-catcher"
    assert data["consumerName"] == "test-consumer"


@pytest.mark.asyncio
async def test_post_streams_route_switches_and_reports():
    fake = FakeRedis(existing_groups={"messages": ["homerun2-led-catcher"]})
    consumer = make_consumer(["messages"], fake)
    transport = ASGITransport(app=build_client_app(consumer))

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/streams", json={"streams": ["tabletennis"]})

    assert resp.status_code == 200
    data = resp.json()
    assert data["streams"] == ["tabletennis"]
    assert data["added"] == ["tabletennis"]
    assert data["removed"] == ["messages"]
    assert consumer.streams == ["tabletennis"]
    assert fake.setids == [("tabletennis", "homerun2-led-catcher", "$")]


@pytest.mark.asyncio
async def test_post_streams_route_honours_skip_backlog_false():
    fake = FakeRedis(existing_groups={"messages": ["homerun2-led-catcher"]})
    consumer = make_consumer(["messages"], fake)
    transport = ASGITransport(app=build_client_app(consumer))

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/streams", json={"streams": ["tabletennis"], "skipBacklog": False})

    assert resp.status_code == 200
    assert fake.setids == []


@pytest.mark.asyncio
async def test_post_streams_route_rejects_an_empty_list():
    consumer = make_consumer(["messages"], FakeRedis())
    transport = ASGITransport(app=build_client_app(consumer))

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/streams", json={"streams": []})

    assert resp.status_code == 400
    assert consumer.streams == ["messages"]
