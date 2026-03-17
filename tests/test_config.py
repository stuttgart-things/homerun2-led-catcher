"""Tests for configuration loading."""

from led_catcher.config import load_config


def test_load_config_defaults(monkeypatch):
    # Clear relevant env vars
    for key in (
        "REDIS_ADDR",
        "REDIS_PORT",
        "REDIS_PASSWORD",
        "REDIS_STREAM",
        "CONSUMER_GROUP",
        "LED_MODE",
        "HEALTH_PORT",
        "LOG_FORMAT",
        "LOG_LEVEL",
    ):
        monkeypatch.delenv(key, raising=False)

    cfg = load_config()
    assert cfg.redis.addr == "localhost"
    assert cfg.redis.port == 6379
    assert cfg.redis.password == ""
    assert cfg.redis.stream == "messages"
    assert cfg.consumer_group == "homerun2-led-catcher"
    assert cfg.led_mode == "full"
    assert cfg.health_port == 8080
    assert cfg.log_format == "json"
    assert cfg.log_level == "info"


def test_load_config_custom(monkeypatch):
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
    assert cfg.redis.stream == "events"
    assert cfg.consumer_group == "my-group"
    assert cfg.led_mode == "web"
    assert cfg.health_port == 9090
    assert cfg.log_format == "text"
    assert cfg.log_level == "debug"
