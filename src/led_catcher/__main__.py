"""Entry point for homerun2-led-catcher."""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

import uvicorn
from fastapi import FastAPI

from led_catcher.config import Config, load_config, setup_logging
from led_catcher.consumer import RedisConsumer
from led_catcher.display import reset_worker
from led_catcher.handlers.control import create_control_router
from led_catcher.handlers.health import health_app, set_build_info, set_consumer_state
from led_catcher.handlers.led_handler import create_led_handler
from led_catcher.handlers.log_handler import log_handler
from led_catcher.profile import load_profile
from led_catcher.web import EventTracker, create_web_app, create_web_handler

logger = logging.getLogger("led_catcher")


def _build_handlers(cfg: Config) -> tuple[list, EventTracker | None]:
    """Compose message handlers based on LED_MODE. Returns (handlers, tracker)."""
    handlers = [log_handler]
    tracker = None

    mode = cfg.led_mode.lower()
    if mode not in ("led", "web", "full"):
        logger.warning("unknown LED_MODE '%s', defaulting to 'full'", mode)
        mode = "full"

    profile = load_profile(cfg.profile_path)

    if mode in ("led", "full"):
        handlers.append(create_led_handler(profile))
        logger.info("LED handler active")

    if mode in ("web", "full"):
        tracker = EventTracker()
        handlers.append(create_web_handler(profile, tracker))
        logger.info("web handler active")

    logger.info("active mode: %s, handlers: %d", mode, len(handlers))
    return handlers, tracker


def _build_app(cfg: Config, tracker: EventTracker | None, consumer: RedisConsumer) -> FastAPI:
    """Build the combined FastAPI app with health + control + optional web simulator."""
    if tracker is not None:
        # Mount health endpoints on the web app
        app = create_web_app(
            tracker,
            cfg.version,
            cfg.commit,
            cfg.date,
            consumer=consumer,
            presets=cfg.ui_stream_presets,
        )
        app.get("/healthz")(health_app.routes[0].endpoint)
        app.get("/health")(health_app.routes[1].endpoint)
    else:
        app = health_app

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
        loop = asyncio.get_running_loop()

        def _signal_handler() -> None:
            logger.info("received shutdown signal")
            stop.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _signal_handler)

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


async def _run(cfg: Config) -> int:
    set_build_info(cfg.version, cfg.commit, cfg.date)
    handlers, tracker = _build_handlers(cfg)
    consumer = RedisConsumer(cfg, handlers)
    set_consumer_state(lambda: consumer.state)

    logger.info(
        "starting homerun2-led-catcher",
        extra={
            "version": cfg.version,
            "commit": cfg.commit,
            "date": cfg.date,
        },
    )
    logger.info(
        "configuration loaded",
        extra={
            "redis_addr": f"{cfg.redis.addr}:{cfg.redis.port}",
            "streams": cfg.redis.streams,
            "consumer_group": cfg.consumer_group,
            "redis_startup_timeout": cfg.redis_startup_timeout,
        },
    )

    # Build combined app (health + control + optional web simulator)
    app = _build_app(cfg, tracker, consumer)

    # Start server in background
    server_config = uvicorn.Config(
        app,
        host="0.0.0.0",  # nosec B104 - intentional for container service
        port=cfg.health_port,
        log_level="warning",
    )
    server = uvicorn.Server(server_config)
    server_task = asyncio.create_task(server.serve())

    if tracker is not None:
        logger.info("web simulator started on port %d", cfg.health_port)
    else:
        logger.info("health server started on port %d", cfg.health_port)

    # Run consumer (blocks until shutdown signal, or until the consumer gives up)
    clean = await _run_consumer(consumer)

    # After the consumer, so nothing is still submitting displays. Leaves the
    # panel dark rather than stuck on the last score.
    reset_worker()

    server.should_exit = True
    await server_task
    if not clean:
        logger.error("led-catcher exited because its consumer stopped")
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
