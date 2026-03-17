"""Entry point for homerun2-led-catcher."""

from __future__ import annotations

import asyncio
import logging
import signal

import uvicorn
from fastapi import FastAPI

from led_catcher.config import Config, load_config, setup_logging
from led_catcher.consumer import RedisConsumer
from led_catcher.handlers.health import health_app, set_build_info
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


def _build_app(cfg: Config, tracker: EventTracker | None) -> FastAPI:
    """Build the combined FastAPI app with health + optional web simulator."""
    if tracker is not None:
        # Mount health endpoints on the web app
        web_app = create_web_app(tracker, cfg.version, cfg.commit, cfg.date)
        web_app.get("/healthz")(health_app.routes[0].endpoint)
        web_app.get("/health")(health_app.routes[1].endpoint)
        return web_app
    return health_app


async def _run_consumer(cfg: Config, handlers: list) -> None:
    consumer = RedisConsumer(cfg, handlers)

    loop = asyncio.get_running_loop()
    stop = asyncio.Event()

    def _signal_handler() -> None:
        logger.info("received shutdown signal")
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    consumer_task = asyncio.create_task(consumer.run())

    await stop.wait()
    await consumer.shutdown()
    consumer_task.cancel()
    try:
        await consumer_task
    except asyncio.CancelledError:
        pass


async def _run(cfg: Config) -> None:
    set_build_info(cfg.version, cfg.commit, cfg.date)
    handlers, tracker = _build_handlers(cfg)

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
            "stream": cfg.redis.stream,
            "consumer_group": cfg.consumer_group,
        },
    )

    # Build combined app (health + optional web simulator)
    app = _build_app(cfg, tracker)

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

    # Run consumer (blocks until shutdown signal)
    await _run_consumer(cfg, handlers)

    server.should_exit = True
    await server_task
    logger.info("led-catcher exited gracefully")


def main() -> None:
    cfg = load_config()
    setup_logging(cfg)
    asyncio.run(_run(cfg))


if __name__ == "__main__":
    main()
