"""Micro-frontend fragments for homerun2-portal.

The portal reads ``{base_path}/ui/manifest.json`` to learn which fragments a
service offers, then loads each ``{base_path}/ui/fragment/{id}`` with htmx into
its own page. A fragment is plain HTML without ``<head>``: it is styled only by
the portal's ``hr-*`` classes (``static/homerun2.css`` in homerun2-portal), and
every URL in it carries the base path, because it is served on the portal's
origin.
"""

from __future__ import annotations

import html

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from led_catcher.web.events import EventTracker, LedEvent

EVENTS_LIMIT = 8
EVENTS_REFRESH_SECONDS = 5

# The portal's severity classes: hr-sev-error, hr-sev-warning, ...
_SEVERITIES = {"error", "critical", "warning", "success", "info", "debug"}


def create_fragments_router(tracker: EventTracker, version: str = "dev", base_path: str = "") -> APIRouter:
    """``/ui/manifest.json`` and the fragments it lists."""
    router = APIRouter()

    @router.get("/ui/manifest.json")
    async def manifest():
        return {
            "name": "homerun2-led-catcher",
            "title": "LED Matrix",
            "version": version,
            "basePath": base_path,
            "fragments": [
                {
                    "id": "events",
                    "title": "Latest on the matrix",
                    "url": f"{base_path}/ui/fragment/events",
                    "refreshSeconds": EVENTS_REFRESH_SECONDS,
                }
            ],
        }

    @router.get("/ui/fragment/events", response_class=HTMLResponse)
    async def events_fragment():
        return HTMLResponse(render_events_fragment(tracker.recent(EVENTS_LIMIT), base_path))

    return router


def render_events_fragment(events: list[LedEvent], base_path: str = "") -> str:
    """The latest events as an ``hr-list``.

    Every field is escaped: titles come from messages and from POST /display,
    and the portal shows the fragment to every viewer.
    """
    if not events:
        rows = '<li class="hr-item hr-muted">No events yet.</li>'
    else:
        rows = "".join(_render_item(e) for e in events)
    return (
        '<div class="hr-frag">'
        f'<ul class="hr-list">{rows}</ul>'
        f'<a class="hr-frag-more" href="{html.escape(base_path)}/">Open simulator →</a>'
        "</div>"
    )


def _render_item(e: LedEvent) -> str:
    severity = e.severity.lower() if e.severity.lower() in _SEVERITIES else "info"
    if severity == "critical":
        severity = "error"
    label = e.severity.upper() or e.kind.upper()
    return (
        '<li class="hr-item">'
        f'<span class="hr-time">{html.escape(e.timestamp)}</span>'
        f'<span class="hr-dot" style="background:{e.color_hex()}"></span>'
        f'<span class="hr-sev hr-sev-{severity}">{html.escape(label)}</span>'
        f'<span class="hr-system">{html.escape(e.system)}</span>'
        f'<span class="hr-title">{html.escape(e.title)}</span>'
        "</li>"
    )
