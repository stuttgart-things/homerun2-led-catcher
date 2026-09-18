"""Standalone mode (#80): no Redis, and no way to keep running with nothing working."""

from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

import led_catcher.__main__ as entry
from led_catcher.__main__ import _resolve_mode, _run_standalone
from led_catcher.config.settings import Config, RedisConfig
from led_catcher.handlers import health


async def _forever() -> None:
    await asyncio.Event().wait()


async def test_a_signal_is_a_clean_exit():
    server = asyncio.create_task(_forever())
    stop = asyncio.Event()
    runner = asyncio.create_task(_run_standalone(server, worker_alive=lambda: True, stop=stop))
    await asyncio.sleep(0.02)
    stop.set()
    assert await asyncio.wait_for(runner, timeout=1.0) is True
    server.cancel()


async def test_a_dead_display_worker_ends_the_process(monkeypatch, caplog):
    # The standalone variant of #65: without this the process would serve
    # /display happily while nothing ever reached the panel.
    monkeypatch.setattr(entry, "WATCHDOG_INTERVAL", 0.01)
    server = asyncio.create_task(_forever())
    alive = {"value": True}
    runner = asyncio.create_task(_run_standalone(server, worker_alive=lambda: alive["value"], stop=asyncio.Event()))
    await asyncio.sleep(0.03)
    alive["value"] = False
    assert await asyncio.wait_for(runner, timeout=1.0) is False
    assert "display worker stopped unexpectedly" in caplog.text
    server.cancel()


async def test_a_web_server_that_ends_ends_the_process(caplog):
    async def fails() -> None:
        raise OSError("address already in use")

    server = asyncio.create_task(fails())
    result = await asyncio.wait_for(_run_standalone(server, worker_alive=lambda: True, stop=asyncio.Event()), 1.0)
    assert result is False
    assert "web server stopped unexpectedly" in caplog.text


@pytest.mark.parametrize(("raw", "mode"), [("standalone", "standalone"), ("STANDALONE", "standalone"), ("x", "full")])
def test_standalone_is_a_known_mode(raw, mode):
    assert _resolve_mode(Config(redis=RedisConfig(), led_mode=raw)) == mode


@pytest.fixture
def display_state():
    state = {"alive": True}
    health.set_display_state(lambda: state["alive"])
    yield state
    health.set_display_state(None)


@pytest.mark.parametrize(("alive", "code", "status"), [(True, 200, "running"), (False, 503, "stopped")])
async def test_health_reports_the_display_worker(display_state, alive, code, status):
    display_state["alive"] = alive
    async with AsyncClient(transport=ASGITransport(app=health.health_app), base_url="http://test") as c:
        res = await c.get("/healthz")
    assert res.status_code == code
    assert res.json()["display"] == status
    assert "consumer" not in res.json()
