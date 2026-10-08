"""A new consumer group must not replay the stream, and old entries are skipped (#123)."""

import time

import pytest
import redis.asyncio as aioredis

from led_catcher.config import load_config
from led_catcher.config.settings import (
    DEFAULT_MAX_MESSAGE_AGE,
    Config,
    RedisConfig,
    parse_max_message_age,
)
from led_catcher.consumer.redis_consumer import RedisConsumer


class FakeRedis:
    """Records group creation and acks; answers JSON.GET with one message."""

    def __init__(self, groups: dict[str, list[str]] | None = None):
        self.groups = groups if groups is not None else {}
        self.created: list[tuple[str, str, str]] = []
        self.acked: list[str] = []

    async def xinfo_groups(self, stream):
        if stream not in self.groups:
            raise aioredis.ResponseError("no such key")
        return [{"name": g} for g in self.groups[stream]]

    async def xgroup_create(self, stream, group, id, mkstream):  # noqa: A002 - redis-py's name
        self.created.append((stream, group, id))

    async def xack(self, stream, group, entry_id):
        self.acked.append(entry_id)

    async def execute_command(self, *args):
        return '[{"title": "t", "message": "m", "severity": "info", "system": "s"}]'


def _consumer(**cfg) -> tuple[RedisConsumer, list]:
    seen = []
    consumer = RedisConsumer(Config(redis=RedisConfig(), **cfg), [seen.append])
    return consumer, seen


def _entry_id(age_seconds: float) -> str:
    return f"{int((time.time() - age_seconds) * 1000)}-0"


@pytest.mark.asyncio
async def test_a_new_group_starts_at_the_end_of_the_stream():
    consumer, _ = _consumer()
    fake = FakeRedis(groups={"alerts": []})

    await consumer._ensure_group(fake, ["alerts", "new-stream"])

    assert fake.created == [
        ("alerts", "homerun2-led-catcher", "$"),
        ("new-stream", "homerun2-led-catcher", "$"),
    ]


@pytest.mark.asyncio
async def test_the_start_id_is_configurable():
    consumer, _ = _consumer(consumer_start_id="0")
    fake = FakeRedis(groups={"messages": []})

    await consumer._ensure_group(fake, ["messages"])

    assert fake.created == [("messages", "homerun2-led-catcher", "0")]


@pytest.mark.asyncio
async def test_an_existing_group_keeps_its_position():
    consumer, _ = _consumer()
    fake = FakeRedis(groups={"messages": ["homerun2-led-catcher"]})

    await consumer._ensure_group(fake, ["messages"])

    assert fake.created == []


@pytest.mark.asyncio
async def test_an_old_entry_is_acked_but_not_shown():
    consumer, seen = _consumer(max_message_age=60.0)
    fake = FakeRedis()
    old = _entry_id(3600)

    await consumer._process_entry(fake, "alerts", "g", old, {"messageID": "m1"})

    assert seen == []
    assert fake.acked == [old]


@pytest.mark.asyncio
async def test_a_fresh_entry_is_shown():
    consumer, seen = _consumer(max_message_age=60.0)
    fake = FakeRedis()
    fresh = _entry_id(5)

    await consumer._process_entry(fake, "messages", "g", fresh, {"messageID": "m1"})

    assert len(seen) == 1
    assert fake.acked == [fresh]


@pytest.mark.asyncio
async def test_max_age_zero_shows_everything():
    consumer, seen = _consumer(max_message_age=0.0)
    fake = FakeRedis()

    await consumer._process_entry(fake, "alerts", "g", _entry_id(86400 * 30), {"messageID": "m1"})

    assert len(seen) == 1


def test_max_message_age_parsing():
    assert parse_max_message_age("") == DEFAULT_MAX_MESSAGE_AGE
    assert parse_max_message_age("5m") == 300.0
    assert parse_max_message_age("0") == 0.0
    with pytest.raises(ValueError, match="MAX_MESSAGE_AGE"):
        parse_max_message_age("soon")


def test_load_config_defaults_and_env(monkeypatch):
    monkeypatch.delenv("CONSUMER_START_ID", raising=False)
    monkeypatch.delenv("MAX_MESSAGE_AGE", raising=False)
    cfg = load_config()
    assert cfg.consumer_start_id == "$"
    assert cfg.max_message_age == DEFAULT_MAX_MESSAGE_AGE

    monkeypatch.setenv("CONSUMER_START_ID", "0")
    monkeypatch.setenv("MAX_MESSAGE_AGE", "10m")
    cfg = load_config()
    assert cfg.consumer_start_id == "0"
    assert cfg.max_message_age == 600.0
