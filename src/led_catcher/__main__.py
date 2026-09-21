"""Entry point for homerun2-led-catcher."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import sys

import uvicorn
from fastapi import FastAPI

from led_catcher.config import Config, load_config, setup_logging
from led_catcher.config.settings import LED_MODES
from led_catcher.consumer import RedisConsumer
from led_catcher.display import get_worker, reset_worker
from led_catcher.handlers.control import create_control_router
from led_catcher.handlers.display_api import create_display_router
from led_catcher.handlers.health import health_app, set_build_info, set_consumer_state, set_display_state
from led_catcher.handlers.led_handler import create_led_handler
from led_catcher.handlers.log_handler import log_handler
from led_catcher.profile import Profile, load_profile
from led_catcher.web import EventTracker, create_web_app, create_web_handler

logger = logging.getLogger("led_catcher")

# How often standalone mode checks that the display worker is still alive.
WATCHDOG_INTERVAL = 1.0


class _Server(uvicorn.Server):
    """A uvicorn server that leaves the signal handlers to us.

    uvicorn's own ``capture_signals()`` swaps in its handler for SIGINT and
    SIGTERM while it serves. Ours, installed with ``loop.add_signal_handler``,
    still fires through the loop's wakeup fd, so one signal started two
    shutdowns: uvicorn's cancelled every open simulator stream, and when
    ``serve()`` returned it re-raised the signal, which logged "received
    shutdown signal" a second time (#105). With this, the process has one
    shutdown path: the ``stop`` event, which also ends the event streams.
    """

    @contextlib.contextmanager
    def capture_signals(self):
        yield


def _resolve_mode(cfg: Config) -> str:
    mode = cfg.led_mode.lower()
    if mode not in LED_MODES:
        logger.warning("unknown LED_MODE '%s', defaulting to 'full'", mode)
        mode = "full"
    return mode


def _build_handlers(cfg: Config, mode: str, profile: Profile) -> tuple[list, EventTracker | None]:
    """Compose message handlers based on LED_MODE. Returns (handlers, tracker)."""
    handlers = [log_handler]
    tracker = None

    if mode in ("led", "full"):
        handlers.append(create_led_handler(profile, panel=cfg.panel))
        logger.info("LED handler active")

    if mode in ("web", "full"):
        tracker = EventTracker()
        handlers.append(create_web_handler(profile, tracker))
        logger.info("web handler active")

    logger.info("active mode: %s, handlers: %d", mode, len(handlers))
    return handlers, tracker


def _build_app(
    cfg: Config,
    mode: str,
    tracker: EventTracker | None,
    consumer: RedisConsumer | None,
    profile: Profile,
    stop: asyncio.Event | None = None,
) -> FastAPI:
    """Build the combined FastAPI app: health, /display, /streams and the optional simulator."""
    if tracker is not None:
        # Mount health endpoints on the web app
        app = create_web_app(
            tracker,
            cfg.version,
            cfg.commit,
            cfg.date,
            consumer=consumer,
            presets=cfg.ui_stream_presets,
            mode=mode,
            display_api=cfg.api.writable,
            stop=stop,
        )
        app.get("/healthz")(health_app.routes[0].endpoint)
        app.get("/health")(health_app.routes[1].endpoint)
    else:
        app = health_app

    # The worker exists already in every mode that drives the panel; `web`
    # has none, and its /display writes reach only the simulator.
    worker = get_worker(panel=cfg.panel) if mode in ("led", "full", "standalone") else None
    app.include_router(create_display_router(worker, cfg.api, tracker=tracker, colors=profile.colors))
    if consumer is not None:
        app.include_router(create_control_router(consumer))
    return app


async def _run_consumer(consumer: RedisConsumer, stop: asyncio.Event | None = None) -> bool:
    """Run the consumer until a shutdown signal or until it ends on its own.

    Returns True for a requested shutdown, False when the consumer task ended
    without one: Redis not ready within REDIS_STARTUP_TIMEOUT, or any crash.
    The caller exits non-zero in that case, so Kubernetes restarts the pod
    visibly instead of it running on with nothing consuming (#65).
    """
    if stop is None:
        stop = asyncio.Event()
        _install_signal_handlers(stop)

    consumer_task = asyncio.create_task(consumer.run())
    stop_task = asyncio.create_task(stop.wait())
    await asyncio.wait({consumer_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)

    if not stop.is_set():
        stop_task.cancel()
        consumer.mark_failed()
        exc = consumer_task.exception() if not consumer_task.cancelled() else None
        logger.error("consumer stopped unexpectedly, exiting", extra={"error": repr(exc)})
        await consumer.shutdown()
        return False

    await consumer.shutdown()
    consumer_task.cancel()
    try:
        await consumer_task
    except asyncio.CancelledError:
        pass
    except Exception:  # noqa: BLE001 - shutting down anyway
        logger.exception("consumer raised during shutdown")
    return True


def _install_signal_handlers(stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()

    def _signal_handler() -> None:
        logger.info("received shutdown signal")
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)


async def _run_standalone(server_task: asyncio.Task, worker_alive, stop: asyncio.Event | None = None) -> bool:
    """Wait for a shutdown signal in standalone mode, where there is no consumer.

    Returns True for a requested shutdown, False when the web server or the
    display worker ended on their own. Either one ending is the standalone
    version of #65 — a process that keeps running with nothing it can do — so
    the caller exits non-zero and the pod restarts visibly.
    """
    if stop is None:
        stop = asyncio.Event()
        _install_signal_handlers(stop)

    async def _watch_worker() -> None:
        while worker_alive():
            await asyncio.sleep(WATCHDOG_INTERVAL)

    stop_task = asyncio.create_task(stop.wait())
    watchdog = asyncio.create_task(_watch_worker())
    done, _ = await asyncio.wait({stop_task, server_task, watchdog}, return_when=asyncio.FIRST_COMPLETED)
    stop_task.cancel()
    watchdog.cancel()

    if stop.is_set():
        return True
    if server_task in done:
        exc = server_task.exception() if not server_task.cancelled() else None
        logger.error("web server stopped unexpectedly, exiting", extra={"error": repr(exc)})
    else:
        logger.error("display worker stopped unexpectedly, exiting")
    return False


async def _run(cfg: Config) -> int:
    set_build_info(cfg.version, cfg.commit, cfg.date)
    mode = _resolve_mode(cfg)
    standalone = mode == "standalone"

    logger.info(
        "starting homerun2-led-catcher",
        extra={
            "version": cfg.version,
            "commit": cfg.commit,
            "date": cfg.date,
            "mode": mode,
        },
    )

    # Installed before anything starts, so a signal during startup is not lost.
    stop = asyncio.Event()
    _install_signal_handlers(stop)

    profile = load_profile(cfg.profile_path, rules_expected=not standalone)
    consumer: RedisConsumer | None = None
    if standalone:
        # No Redis, no consumer, no rule matching: the /display API is the
        # only thing that puts anything on the panel (#80). The simulator
        # tracker is filled by that API, so the canvas shows the panel.
        tracker = EventTracker()
        worker = get_worker(panel=cfg.panel)
        set_consumer_state(None)
        set_display_state(lambda: worker.alive)
        logger.info("standalone mode: no Redis — the panel is driven by the /display API")
        if not cfg.api.writable:
            logger.warning("standalone mode without LED_API_TOKEN: nothing can write to the panel")
    else:
        handlers, tracker = _build_handlers(cfg, mode, profile)
        consumer = RedisConsumer(cfg, handlers)
        set_consumer_state(lambda: consumer.state)
        logger.info(
            "configuration loaded",
            extra={
                "redis_addr": f"{cfg.redis.addr}:{cfg.redis.port}",
                "streams": cfg.redis.streams,
                "consumer_group": cfg.consumer_group,
                "redis_startup_timeout": cfg.redis_startup_timeout,
            },
        )

    # Build combined app (health + display + control + optional web simulator)
    app = _build_app(cfg, mode, tracker, consumer, profile, stop=stop)

    # Start server in background
    server_config = uvicorn.Config(
        app,
        host="0.0.0.0",  # nosec B104 - intentional for container service
        port=cfg.health_port,
        log_level="warning",
    )
    server = _Server(server_config)
    server_task = asyncio.create_task(server.serve())

    if tracker is not None:
        logger.info("web simulator started on port %d", cfg.health_port)
    else:
        logger.info("health server started on port %d", cfg.health_port)

    if standalone:
        clean = await _run_standalone(server_task, worker_alive=lambda: worker.alive, stop=stop)
    else:
        # Run consumer (blocks until shutdown signal, or until the consumer gives up)
        clean = await _run_consumer(consumer, stop=stop)

    # After the consumer, so nothing is still submitting displays. Leaves the
    # panel dark rather than stuck on the last score.
    reset_worker()
    set_display_state(None)

    server.should_exit = True
    if not server_task.done():
        await server_task
    if not clean:
        what = "the web server or display worker" if standalone else "its consumer"
        logger.error("led-catcher exited because %s stopped", what)
        return 1
    logger.info("led-catcher exited gracefully")
    return 0


def main() -> None:
    try:
        cfg = load_config()
    except ValueError as exc:
        print(f"invalid configuration: {exc}", file=sys.stderr)
        sys.exit(1)
    setup_logging(cfg)
    sys.exit(asyncio.run(_run(cfg)))


if __name__ == "__main__":
    main()
