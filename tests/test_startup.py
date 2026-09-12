"""Startup resilience: REDIS_STARTUP_TIMEOUT, the consumer's Redis wait, health and exit (#65)."""

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

import led_catcher.consumer.redis_consumer as rc
from led_catcher.__main__ import _run_consumer
from led_catcher.config.settings import (
    DEFAULT_REDIS_STARTUP_TIMEOUT,
    Config,
    RedisConfig,
    _JsonFormatter,
    load_config,
    parse_duration,
    parse_redis_startup_timeout,
)
from led_catcher.consumer.redis_consumer import RedisConsumer, RedisNotReadyError
from led_catcher.handlers import health


@pytest.mark.parametrize(
    ("raw", "seconds"),
    [("90s", 90.0), ("2m", 120.0), ("1m30s", 90.0), ("500ms", 0.5), ("1.5s", 1.5), ("1h", 3600.0), ("0", 0.0)],
)
def test_parse_duration_valid(raw, seconds):
    assert parse_duration(raw) == pytest.approx(seconds)


@pytest.mark.parametrize("raw", ["120", "soon", "", "s", "5 s", "1x", "1m30"])
def test_parse_duration_invalid(raw):
    with pytest.raises(ValueError):
        parse_duration(raw)


def test_startup_timeout_default_when_unset():
    assert parse_redis_startup_timeout("") == DEFAULT_REDIS_STARTUP_TIMEOUT
    assert parse_redis_startup_timeout("   ") == DEFAULT_REDIS_STARTUP_TIMEOUT


@pytest.mark.parametrize("raw", ["0s", "0", "-10s", "soon", "120"])
def test_startup_timeout_rejects_invalid(raw):
    with pytest.raises(ValueError, match="REDIS_STARTUP_TIMEOUT"):
        parse_redis_startup_timeout(raw)


def test_load_config_reads_startup_timeout(monkeypatch):
    monkeypatch.setenv("REDIS_STARTUP_TIMEOUT", "45s")
    assert load_config().redis_startup_timeout == 45.0


def test_load_config_fails_on_invalid_startup_timeout(monkeypatch):
    monkeypatch.setenv("REDIS_STARTUP_TIMEOUT", "soon")
    with pytest.raises(ValueError, match="REDIS_STARTUP_TIMEOUT"):
        load_config()


def _consumer(timeout: float = 5.0) -> RedisConsumer:
    return RedisConsumer(Config(redis=RedisConfig(addr="127.0.0.1", port=1), redis_startup_timeout=timeout), [])


@pytest.fixture
def fast_backoff(monkeypatch):
    monkeypatch.setattr(rc, "STARTUP_BACKOFF_INITIAL_SECONDS", 0.01)
    monkeypatch.setattr(rc, "STARTUP_BACKOFF_MAX_SECONDS", 0.02)


async def test_wait_ready_retries_until_redis_answers(fast_backoff):
    consumer = _consumer()
    calls = 0

    async def attempt():
        nonlocal calls
        calls += 1
        if calls < 3:
            raise ConnectionError("connection refused")

    consumer._startup_attempt = attempt
    await consumer.wait_ready(timeout=2.0)
    assert calls == 3
    assert consumer.state == "waiting_for_redis"  # "running" is set by run() once the read loop starts


async def test_wait_ready_gives_up_after_the_budget(fast_backoff):
    consumer = _consumer()
    calls = 0

    async def attempt():
        nonlocal calls
        calls += 1
        raise ConnectionError("connection refused")

    consumer._startup_attempt = attempt
    with pytest.raises(RedisNotReadyError, match="not ready after"):
        await consumer.wait_ready(timeout=0.1)
    assert calls >= 2


async def test_wait_ready_ends_promptly_when_cancelled():
    consumer = _consumer()

    async def attempt():
        raise ConnectionError("connection refused")

    consumer._startup_attempt = attempt
    task = asyncio.create_task(consumer.wait_ready(timeout=60.0))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1.0)


class _FakeConsumer:
    def __init__(self, run):
        self._run = run
        self.state = "waiting_for_redis"
        self.shutdown_called = False

    async def run(self):
        await self._run(self)

    def mark_failed(self):
        if self.state != "stopped":
            self.state = "failed"

    async def shutdown(self):
        self.shutdown_called = True
        if self.state != "failed":
            self.state = "stopped"


async def test_run_consumer_reports_a_consumer_that_gave_up(fast_backoff):
    # The real RedisConsumer, not a fake: its shutdown() must not turn "failed"
    # back into "stopped", or /healthz reports 200 until the process exits.
    consumer = _consumer(timeout=0.05)

    async def attempt():
        raise ConnectionError("connection refused")

    consumer._startup_attempt = attempt
    assert await _run_consumer(consumer, stop=asyncio.Event()) is False
    assert consumer.state == "failed"


async def test_run_consumer_clean_shutdown_while_waiting():
    async def waits_forever(_self):
        await asyncio.sleep(3600)

    consumer = _FakeConsumer(waits_forever)
    stop = asyncio.Event()
    runner = asyncio.create_task(_run_consumer(consumer, stop=stop))
    await asyncio.sleep(0.05)
    stop.set()
    assert await asyncio.wait_for(runner, timeout=1.0) is True
    assert consumer.state == "stopped"


@pytest.fixture
def consumer_state():
    state = {"value": "waiting_for_redis"}
    health.set_consumer_state(lambda: state["value"])
    yield state
    health.set_consumer_state(None)


@pytest.mark.parametrize(
    ("value", "code", "status"),
    [
        ("waiting_for_redis", 200, "ok"),
        ("running", 200, "ok"),
        ("stopped", 200, "ok"),
        ("failed", 503, "unhealthy"),
    ],
)
async def test_healthz_reflects_the_consumer(consumer_state, value, code, status):
    consumer_state["value"] = value
    transport = ASGITransport(app=health.health_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/healthz")
    assert resp.status_code == code
    assert resp.json()["status"] == status
    assert resp.json()["consumer"] == value


def test_json_log_carries_the_retry_fields():
    import json
    import logging

    record = logging.LogRecord("led_catcher", logging.WARNING, __file__, 1, "redis not ready, retrying", None, None)
    record.attempt = 3
    record.next_sleep = 4.0
    record.error = "ConnectionError('refused')"
    entry = json.loads(_JsonFormatter().format(record))
    assert entry["attempt"] == 3
    assert entry["next_sleep"] == 4.0
    assert entry["error"] == "ConnectionError('refused')"
