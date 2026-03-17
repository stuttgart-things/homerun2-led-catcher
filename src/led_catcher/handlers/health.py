"""Health endpoint — /healthz and /health returning JSON status."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.responses import JSONResponse

health_app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

_build_info: dict = {"version": "dev", "commit": "unknown", "date": "unknown"}


def set_build_info(version: str, commit: str, date: str) -> None:
    _build_info["version"] = version
    _build_info["commit"] = commit
    _build_info["date"] = date


@health_app.get("/healthz")
@health_app.get("/health")
async def healthz() -> JSONResponse:
    return JSONResponse({
        "status": "ok",
        "time": datetime.now(timezone.utc).isoformat(),
        **_build_info,
    })
