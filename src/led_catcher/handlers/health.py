"""Health endpoint — /healthz and /health returning JSON status."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from fastapi import FastAPI
from fastapi.responses import JSONResponse

health_app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

_build_info: dict = {"version": "dev", "commit": "unknown", "date": "unknown"}


_consumer_state: Callable[[], str] | None = None


def set_consumer_state(provider: Callable[[], str] | None) -> None:
    """Register what ``/healthz`` reports as the consumer's state.

    Healthy while the consumer waits for Redis or runs, so liveness does not kill
    a pod that is inside its ``REDIS_STARTUP_TIMEOUT``. ``failed`` (the consumer
    task ended without a shutdown request) makes the endpoint answer 503 (#65).
    """
    global _consumer_state
    _consumer_state = provider


_display_alive: Callable[[], bool] | None = None


def set_display_state(provider: Callable[[], bool] | None) -> None:
    """Register whether the display worker thread is alive.

    In standalone mode there is no consumer whose state could stand for the
    process's health, and a dead display thread is that mode's version of #65:
    a pod that stays up and does nothing. So a dead worker answers 503.
    """
    global _display_alive
    _display_alive = provider


def set_build_info(version: str, commit: str, date: str) -> None:
    _build_info["version"] = version
    _build_info["commit"] = commit
    _build_info["date"] = date


@health_app.get("/healthz")
@health_app.get("/health")
async def healthz() -> JSONResponse:
    """Liveness and build info. 503 when the consumer failed or, in standalone mode,
    the display worker stopped."""
    body: dict = {
        "status": "ok",
        "time": datetime.now(timezone.utc).isoformat(),
        **_build_info,
    }
    status_code = 200
    if _consumer_state is not None:
        state = _consumer_state()
        body["consumer"] = state
        if state == "failed":
            body["status"] = "unhealthy"
            status_code = 503
    if _display_alive is not None:
        alive = _display_alive()
        body["display"] = "running" if alive else "stopped"
        if not alive:
            body["status"] = "unhealthy"
            status_code = 503
    return JSONResponse(body, status_code=status_code)
