"""One shutdown path (#105): the stop event, not uvicorn's signal capture.

On the Pi, every shutdown logged "received shutdown signal" twice and one
"ASGI callable returned without completing response" per open simulator stream:
uvicorn's own signal handler started a second shutdown that cancelled the
streams, and re-raised the signal once it was done.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import socket
import time

import httpx
import uvicorn

from led_catcher.__main__ import _Server
from led_catcher.web import EventTracker, create_web_app
from led_catcher.web.app import event_stream


def _never_disconnected():
    async def _f() -> bool:
        return False

    return _f


async def test_the_event_stream_ends_when_stop_is_set():
    stop = asyncio.Event()
    # An interval far longer than the test: ending must come from stop, not the clock.
    gen = event_stream(EventTracker(), _never_disconnected(), interval=60, stop=stop)

    async def drain() -> list[dict]:
        return [event async for event in gen]

    runner = asyncio.create_task(drain())
    await asyncio.sleep(0.05)
    started = time.monotonic()
    stop.set()

    assert await asyncio.wait_for(runner, timeout=1.0) == []
    assert time.monotonic() - started < 0.5


async def test_a_stream_opened_after_stop_ends_at_once():
    stop = asyncio.Event()
    stop.set()
    gen = event_stream(EventTracker(), _never_disconnected(), interval=60, stop=stop)

    assert await asyncio.wait_for(anext(gen, None), timeout=1.0) is None


def test_the_server_leaves_the_signal_handlers_alone():
    def ours(signum, frame):  # pragma: no cover - never delivered
        pass

    previous = {sig: signal.signal(sig, ours) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        server = _Server(uvicorn.Config(app=None))
        with server.capture_signals():
            assert signal.getsignal(signal.SIGTERM) is ours
            assert signal.getsignal(signal.SIGINT) is ours
        assert signal.getsignal(signal.SIGTERM) is ours
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def test_shutdown_with_an_open_stream_completes_the_response(caplog):
    stop = asyncio.Event()
    app = create_web_app(EventTracker(), stop=stop)
    port = _free_port()
    server = _Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    serving = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.01)

    caplog.set_level(logging.ERROR)
    async with httpx.AsyncClient() as client:
        async with client.stream("GET", f"http://127.0.0.1:{port}/api/events/stream") as response:
            assert response.status_code == 200
            # What __main__ does on a signal: stop first, then the server.
            stop.set()
            server.should_exit = True
            body = [chunk async for chunk in response.aiter_bytes()]

    await asyncio.wait_for(serving, timeout=5.0)
    assert body is not None, "the stream ended instead of hanging"
    assert "ASGI callable returned without completing response" not in caplog.text
