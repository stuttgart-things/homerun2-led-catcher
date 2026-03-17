"""FastAPI web application for the HTMX LED matrix simulator."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

from led_catcher.web.events import EventTracker

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"


def create_web_app(
    tracker: EventTracker,
    version: str = "dev",
    commit: str = "unknown",
    date: str = "unknown",
) -> FastAPI:
    """Create the HTMX simulator FastAPI app."""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def index():
        template = (TEMPLATES_DIR / "index.html").read_text()
        events_html = _render_events(tracker)
        return (
            template.replace("{{ events_content }}", events_html)
            .replace("{{ version }}", version)
            .replace("{{ commit }}", commit[:7] if len(commit) > 7 else commit)
            .replace("{{ date }}", date)
            .replace("{{ total_events }}", str(tracker.total))
        )

    @app.get("/events", response_class=HTMLResponse)
    async def events_partial():
        return HTMLResponse(_render_events(tracker))

    @app.get("/stats", response_class=HTMLResponse)
    async def stats_partial():
        return HTMLResponse(f'<span id="stats-count">{tracker.total}</span>')

    @app.get("/api/events")
    async def api_events():
        return [
            {
                "timestamp": e.timestamp,
                "severity": e.severity,
                "system": e.system,
                "title": e.title,
                "author": e.author,
                "kind": e.kind,
                "color": e.color_hex(),
            }
            for e in tracker.recent(50)
        ]

    @app.get("/api/events/stream")
    async def events_stream(request: Request):
        async def event_generator():
            last_version = 0
            while True:
                if await request.is_disconnected():
                    break
                current = tracker.version
                if current != last_version:
                    last_version = current
                    events_html = _render_events(tracker)
                    yield {
                        "event": "events-update",
                        "data": events_html,
                    }
                    yield {
                        "event": "stats-update",
                        "data": f'<span id="stats-count">{tracker.total}</span>',
                    }
                await asyncio.sleep(1)

        return EventSourceResponse(event_generator())

    return app


def _render_events(tracker: EventTracker) -> str:
    """Render the events timeline as HTML."""
    events = tracker.recent(30)
    if not events:
        return '<div class="empty-state">No events yet. Waiting for messages...</div>'

    rows = []
    for e in events:
        rows.append(
            f'<div class="event-row">'
            f'<span class="event-time">{e.timestamp}</span>'
            f'<span class="event-dot" style="background:{e.color_hex()}"></span>'
            f'<span class="event-severity {e.severity_css()}">{e.severity.upper()}</span>'
            f'<span class="event-system">{e.system}</span>'
            f'<span class="event-title">{e.title}</span>'
            f"</div>"
        )
    return "\n".join(rows)
