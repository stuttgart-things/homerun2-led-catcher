"""FastAPI web application for the HTMX LED matrix simulator."""

from __future__ import annotations

import asyncio
import html
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI, Form, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

from led_catcher.web.events import EventTracker

if TYPE_CHECKING:
    from led_catcher.consumer import RedisConsumer

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"


def create_web_app(
    tracker: EventTracker,
    version: str = "dev",
    commit: str = "unknown",
    date: str = "unknown",
    consumer: RedisConsumer | None = None,
    presets: list[list[str]] | None = None,
) -> FastAPI:
    """Create the HTMX simulator FastAPI app.

    When a ``consumer`` is passed, the header gains a stream control that shows the
    active subscription and switches it between ``presets``. Without one the
    simulator renders exactly as before and the ``/ui/streams`` routes are not
    registered — there would be nothing for them to act on.
    """
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    presets = presets or []

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def index(response: Response):
        # The page carries the canvas renderer inline, so its behaviour changes
        # with the release. Without this a tab left open across a deploy keeps
        # the old JavaScript while the event timeline, fed by SSE, stays
        # current — so the UI looks alive and disagrees with the matrix, which
        # is a hard thing to spot. The version in the footer is the only clue,
        # and it is the part nobody reads.
        response.headers["Cache-Control"] = "no-store"
        template = (TEMPLATES_DIR / "index.html").read_text()
        events_html = _render_events(tracker)
        page = (
            template.replace("{{ events_content }}", events_html)
            .replace("{{ version }}", version)
            .replace("{{ commit }}", commit[:7] if len(commit) > 7 else commit)
            .replace("{{ date }}", date)
            .replace("{{ total_events }}", str(tracker.total))
        )
        # Substituted last: stream names are user-supplied, and an earlier pass would
        # let one containing a placeholder be rewritten by a later replace().
        return page.replace("{{ streams_control }}", _render_streams_control(consumer, presets))

    @app.get("/events", response_class=HTMLResponse)
    async def events_partial():
        return HTMLResponse(_render_events(tracker))

    @app.get("/stats", response_class=HTMLResponse)
    async def stats_partial():
        return HTMLResponse(f'<span id="stats-count">{tracker.total}</span>')

    if consumer is not None:

        @app.get("/ui/streams", response_class=HTMLResponse)
        async def streams_control_partial():
            return HTMLResponse(_render_streams_control(consumer, presets))

        @app.post("/ui/streams", response_class=HTMLResponse)
        async def switch_streams(streams: str = Form(...)):
            """Switch the subscription from the UI and answer with the control partial.

            htmx posts form-encoded, so this takes ``streams`` as a ``|``-separated
            field rather than the JSON body ``POST /streams`` expects. It is the same
            ``set_streams()`` underneath; the JSON API stays untouched for machine
            callers.

            Failures answer 200 with the error rendered into the partial rather than a
            4xx: htmx does not swap error responses by default, and a control that
            silently does nothing is worse than one that says why.
            """
            try:
                await consumer.set_streams([s for s in streams.split("|")])
            except ValueError as exc:
                return HTMLResponse(_render_streams_control(consumer, presets, error=str(exc)))
            except Exception as exc:
                logger.exception("UI stream switch failed")
                return HTMLResponse(_render_streams_control(consumer, presets, error=f"switch failed: {exc}"))
            return HTMLResponse(_render_streams_control(consumer, presets))

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
                "duration": e.duration,
                "hold": e.hold,
            }
            for e in tracker.recent(50)
        ]

    @app.get("/api/events/stream")
    async def events_stream(request: Request):
        return EventSourceResponse(event_stream(tracker, request.is_disconnected, consumer, presets))

    return app


async def event_stream(
    tracker: EventTracker,
    is_disconnected: Callable[[], Awaitable[bool]],
    consumer: RedisConsumer | None = None,
    presets: list[list[str]] | None = None,
    interval: float = 1.0,
) -> AsyncIterator[dict]:
    """Yield SSE events until the client goes away.

    Kept out of the route so it can be driven directly in tests — httpx's
    ASGITransport buffers the whole response, so an endless generator behind it
    never produces a line.
    """
    presets = presets or []
    last_version = 0
    last_streams: list[str] | None = None
    while True:
        if await is_disconnected():
            break
        current = tracker.version
        if current != last_version:
            last_version = current
            yield {
                "event": "events-update",
                "data": _render_events(tracker),
            }
            yield {
                "event": "stats-update",
                "data": f'<span id="stats-count">{tracker.total}</span>',
            }
        if consumer is not None:
            # Picks up switches made through the JSON API or by another client, so the
            # UI never shows a stale set. Also fires once on connect.
            current_streams = consumer.streams
            if current_streams != last_streams:
                last_streams = current_streams
                yield {
                    "event": "streams-update",
                    "data": _render_streams_control(consumer, presets),
                }
        await asyncio.sleep(interval)


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


def _preset_button(group: list[str], active: bool) -> str:
    """One preset button, posting its group form-encoded to /ui/streams."""
    label = html.escape(" + ".join(group))
    vals = html.escape(json.dumps({"streams": "|".join(group)}), quote=True)
    cls = "stream-preset active" if active else "stream-preset"
    return (
        f'<button type="button" class="{cls}"'
        f' hx-post="/ui/streams" hx-vals="{vals}"'
        f' hx-target="#streams-control" hx-swap="innerHTML">{label}</button>'
    )


def _render_streams_control(
    consumer: RedisConsumer | None,
    presets: list[list[str]],
    error: str | None = None,
) -> str:
    """Render the header stream control.

    Stream names are escaped: /ui/streams is unauthenticated and a name set through
    it persists in the consumer and is then served to every viewer.
    """
    if consumer is None:
        return ""

    active = consumer.streams
    configured = consumer.configured_streams
    overridden = consumer.is_overridden

    parts = [f'<span class="stream-label">Streams:</span> <strong>{html.escape(" + ".join(active))}</strong>']

    if overridden:
        parts.append('<span class="stream-override">overridden</span>')

    buttons = [_preset_button(group, group == active) for group in presets]
    if overridden and configured not in presets:
        buttons.append(_preset_button(configured, False))
    if buttons:
        parts.append(f'<span class="stream-presets">{"".join(buttons)}</span>')

    if overridden:
        vals = html.escape(json.dumps({"streams": "|".join(configured)}), quote=True)
        parts.append(
            f'<button type="button" class="stream-reset"'
            f' hx-post="/ui/streams" hx-vals="{vals}"'
            f' hx-target="#streams-control" hx-swap="innerHTML"'
            f' title="Back to the configured streams">reset</button>'
        )

    if error:
        parts.append(f'<span class="stream-error">{html.escape(error)}</span>')

    return "".join(parts)
