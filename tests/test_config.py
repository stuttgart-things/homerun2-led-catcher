"""Tests for configuration loading."""

import pytest

from led_catcher.config import load_config
from led_catcher.config.settings import parse_streams


def _clear_env(monkeypatch):
    for key in (
        "REDIS_ADDR",
        "REDIS_PORT",
        "REDIS_PASSWORD",
        "REDIS_STREAM",
        "REDIS_STREAMS",
        "CONSUMER_GROUP",
        "LED_MODE",
        "HEALTH_PORT",
        "LOG_FORMAT",
        "LOG_LEVEL",
    ):
        monkeypatch.delenv(key, raising=False)


def test_load_config_defaults(monkeypatch):
    _clear_env(monkeypatch)

    cfg = load_config()
    assert cfg.redis.addr == "localhost"
    assert cfg.redis.port == 6379
    assert cfg.redis.password == ""
    assert cfg.redis.streams == ["messages"]
    assert cfg.redis.stream == "messages"  # legacy accessor
    assert cfg.consumer_group == "homerun2-led-catcher"
    assert cfg.led_mode == "full"
    assert cfg.health_port == 8080
    assert cfg.log_format == "json"
    assert cfg.log_level == "info"


def test_load_config_custom_legacy_single_stream(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("REDIS_ADDR", "redis.example.com")
    monkeypatch.setenv("REDIS_PORT", "6380")
    monkeypatch.setenv("REDIS_PASSWORD", "secret")
    monkeypatch.setenv("REDIS_STREAM", "events")
    monkeypatch.setenv("CONSUMER_GROUP", "my-group")
    monkeypatch.setenv("LED_MODE", "web")
    monkeypatch.setenv("HEALTH_PORT", "9090")
    monkeypatch.setenv("LOG_FORMAT", "text")
    monkeypatch.setenv("LOG_LEVEL", "debug")

    cfg = load_config()
    assert cfg.redis.addr == "redis.example.com"
    assert cfg.redis.port == 6380
    assert cfg.redis.password == "secret"
    assert cfg.redis.streams == ["events"]
    assert cfg.redis.stream == "events"
    assert cfg.consumer_group == "my-group"
    assert cfg.led_mode == "web"
    assert cfg.health_port == 9090
    assert cfg.log_format == "text"
    assert cfg.log_level == "debug"


def test_load_config_multi_stream(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("REDIS_STREAMS", "homerun,releases")
    # Legacy var is ignored when REDIS_STREAMS is set
    monkeypatch.setenv("REDIS_STREAM", "ignored")

    cfg = load_config()
    assert cfg.redis.streams == ["homerun", "releases"]
    assert cfg.redis.stream == "homerun"  # legacy accessor returns first


@pytest.mark.parametrize(
    "streams_env,stream_fallback,expected",
    [
        ("homerun,releases", "ignored", ["homerun", "releases"]),
        (" homerun , releases ", "", ["homerun", "releases"]),
        ("homerun,,releases,", "", ["homerun", "releases"]),
        ("", "homerun", ["homerun"]),
        ("", "messages", ["messages"]),
        ("", "", ["messages"]),
        (" , , ", "legacy", ["legacy"]),
    ],
)
def test_parse_streams(streams_env, stream_fallback, expected):
    assert parse_streams(streams_env, stream_fallback) == expected


# --- the socket timeout has to outlast a blocking read (#56) ---------------


def test_the_socket_timeout_outlasts_a_blocking_read():
    """A blocking XREADGROUP holds the socket for BLOCK_MS.

    redis-py defaults socket_timeout to 5 seconds, which is exactly BLOCK_MS:
    the server answers with nil at that instant and the client has already
    given up, so the read raises instead of returning empty. The loop then
    lands in its error branch and only comes round after the retry delay —
    which is how a stream switch came to take about a minute rather than the
    documented BLOCK_MS.

    Measured against redis-py 8.1.0: block=4000 returns in 4.0s, block=5000
    raises after 59s, block=5000 with a larger socket timeout returns in 5.0s.
    """
    from led_catcher.consumer.redis_consumer import BLOCK_MS, SOCKET_TIMEOUT_SECONDS

    assert SOCKET_TIMEOUT_SECONDS > BLOCK_MS / 1000, (
        "a blocking read of BLOCK_MS would time out on the socket before the server answers it"
    )


async def test_the_consumer_asks_for_that_socket_timeout():
    """The constant is only worth anything if the client is built with it."""
    from led_catcher.config.settings import Config, RedisConfig
    from led_catcher.consumer.redis_consumer import SOCKET_TIMEOUT_SECONDS, RedisConsumer

    consumer = RedisConsumer(Config(redis=RedisConfig()), handlers=[])
    client = await consumer._connect()
    try:
        kwargs = client.connection_pool.connection_kwargs
        assert kwargs.get("socket_timeout") == SOCKET_TIMEOUT_SECONDS, (
            f"the client was built with socket_timeout={kwargs.get('socket_timeout')!r}, "
            "so a blocking read can still outlive it"
        )
    finally:
        await consumer.shutdown()
