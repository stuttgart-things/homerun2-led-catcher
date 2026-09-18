"""The /display API — put something on the panel directly, without a message (#80).

`POST /display` takes a DisplayConfig over HTTP and hands it to the display
worker, exactly as a matched message would be handed over. `GET /display`
says what is on the panel, `DELETE /display` blanks it.

The writing endpoints need `LED_API_TOKEN` as a bearer token. Without one they
are not registered at all: this is an API that writes to a physical display in
a room, so it fails closed. Writes are also capped in length and rate-limited
across all callers — the panel is a shared object, and a tight loop against it
is a denial of service on everyone who can see it.

Every write is logged and, when the simulator runs, recorded in its
EventTracker, so the JSON logs, the event timeline and the canvas all see what
the panel sees.
"""

from __future__ import annotations

import logging
import secrets
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Callable, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from led_catcher.config.settings import ApiConfig
from led_catcher.display.modes import _resolve_image, fonts_dirs, is_bare_name, visual_aid_dirs
from led_catcher.profile.engine import DEFAULT_COLORS, DisplayConfig

if TYPE_CHECKING:
    from led_catcher.display.worker import DisplayWorker
    from led_catcher.web.events import EventTracker

logger = logging.getLogger(__name__)

Kind = Literal["static", "text", "ticker", "image", "gif", "score"]
TEXT_KINDS = {"static", "text", "ticker", "score"}
IMAGE_KINDS = {"image", "gif"}

# Longest a finite display may ask for. Anything meant to stay up longer is
# what `hold` is for, and a held display gives way to the next one.
MAX_DURATION_SECONDS = 600.0

IMAGE_SUFFIXES = {".png", ".gif", ".jpg", ".jpeg", ".bmp", ".webp"}

# What the simulator timeline shows as the source of an API display.
API_SYSTEM = "api"


class DisplayRequest(BaseModel):
    """Body of ``POST /display`` — a DisplayConfig without the rule-matching fields."""

    model_config = ConfigDict(extra="forbid")

    kind: Kind = "text"
    text: str = ""
    image: str = ""
    font: str = "6x10.bdf"
    # An RGB triple, or the name of a profile colour ("error", "warning", …).
    color: tuple[int, int, int] | str = (255, 255, 255)
    duration: float = Field(default=5.0, gt=0, le=MAX_DURATION_SECONDS)
    hold: bool = False

    @field_validator("color")
    @classmethod
    def _rgb_in_range(cls, value):
        if isinstance(value, tuple) and not all(0 <= c <= 255 for c in value):
            raise ValueError("RGB components must be between 0 and 255")
        return value

    @field_validator("font", "image")
    @classmethod
    def _bare_name(cls, value: str) -> str:
        if value and not is_bare_name(value):
            raise ValueError("must be a file name, not a path")
        return value


class RateLimiter:
    """At most `limit` events in any `window` seconds. Thread-safe."""

    def __init__(self, limit: int, window: float = 60.0, clock: Callable[[], float] = time.monotonic) -> None:
        self._limit = limit
        self._window = window
        self._clock = clock
        self._hits: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self) -> float:
        """Take a slot. Returns 0 on success, else seconds until one frees up."""
        with self._lock:
            now = self._clock()
            while self._hits and now - self._hits[0] >= self._window:
                self._hits.popleft()
            if len(self._hits) >= self._limit:
                return self._window - (now - self._hits[0])
            self._hits.append(now)
            return 0.0


def list_assets(dirs, suffixes: set[str]) -> list[str]:
    """File names with one of `suffixes` in the first directories that have them, deduplicated."""
    names: set[str] = set()
    for directory in dirs:
        if not directory.is_dir():
            continue
        for path in directory.iterdir():
            if path.is_file() and path.suffix.lower() in suffixes and is_bare_name(path.name):
                names.add(path.name)
    return sorted(names)


def describe(showing) -> dict:
    """The ``GET /display`` body."""
    if showing is None:
        return {"showing": False, "held": False, "display": None, "since": None}
    return {
        "showing": True,
        "held": showing.held,
        "since": datetime.fromtimestamp(showing.since, timezone.utc).isoformat(),
        "display": describe_config(showing.config),
    }


def create_display_router(
    worker: DisplayWorker | None,
    api: ApiConfig,
    tracker: EventTracker | None = None,
    colors: dict[str, tuple[int, int, int]] | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> APIRouter:
    """Build the ``/display`` router.

    ``worker`` is None when nothing drives a panel (``LED_MODE=web``); writes
    then reach only the simulator. ``colors`` are the profile's named colours,
    which a request can use instead of an RGB triple.
    """
    router = APIRouter()
    palette = dict(DEFAULT_COLORS)
    palette.update(colors or {})

    @router.get("/display")
    async def get_display() -> dict:
        # Unauthenticated: it says nothing that is not already lit up in the room.
        body = describe(worker.showing if worker is not None else None)
        body["writable"] = api.writable
        return body

    @router.get("/display/options")
    async def get_options() -> dict:
        """What a request can name — for the simulator's control form, and for humans."""
        return {
            "kinds": sorted(TEXT_KINDS | IMAGE_KINDS),
            "fonts": list_assets(fonts_dirs(), {".bdf"}),
            "images": list_assets(visual_aid_dirs(), IMAGE_SUFFIXES),
            "colors": {name: list(rgb) for name, rgb in palette.items()},
            "maxText": api.max_text,
            "maxDuration": MAX_DURATION_SECONDS,
            "writable": api.writable,
        }

    if not api.writable:
        logger.info("LED_API_TOKEN not set — POST/DELETE /display are not registered")
        return router

    expected = f"Bearer {api.token}".encode()
    limiter = RateLimiter(api.rate_limit, clock=clock)

    def authorize(request: Request) -> None:
        given = request.headers.get("authorization", "").encode()
        if not secrets.compare_digest(given, expected):
            raise HTTPException(status_code=401, detail="missing or wrong bearer token")
        retry_after = limiter.acquire()
        if retry_after:
            raise HTTPException(
                status_code=429,
                detail=f"rate limit: {api.rate_limit} writes per minute",
                headers={"Retry-After": str(max(1, int(retry_after + 0.999)))},
            )

    @router.post("/display", dependencies=[Depends(authorize)])
    async def post_display(body: DisplayRequest) -> dict:
        config = _to_config(body, palette, api.max_text)
        logger.info(
            "displaying from API: kind=%s hold=%s",
            config.kind,
            config.hold,
            extra={"kind": config.kind, "system": API_SYSTEM},
        )
        if worker is not None:
            worker.submit(config)
        if tracker is not None:
            _record(tracker, config)
        return {"accepted": True, "panel": worker is not None, "display": describe_config(config)}

    @router.delete("/display", dependencies=[Depends(authorize)])
    async def delete_display() -> dict:
        logger.info("blanking the panel from API", extra={"system": API_SYSTEM})
        if worker is not None:
            worker.blank()
        if tracker is not None:
            _record(tracker, DisplayConfig(kind="clear", text="", color=(0, 0, 0), duration=0))
        return {"accepted": True, "panel": worker is not None}

    return router


def describe_config(config: DisplayConfig) -> dict:
    return {
        "kind": config.kind,
        "text": config.text,
        "image": config.image,
        "font": config.font,
        "color": list(config.color),
        "duration": config.duration,
        "hold": config.hold,
    }


def _to_config(body: DisplayRequest, palette: dict[str, tuple[int, int, int]], max_text: int) -> DisplayConfig:
    """Validate what the kind needs and build the DisplayConfig. Raises 400/404."""
    if len(body.text) > max_text:
        raise HTTPException(status_code=400, detail=f"text is longer than {max_text} characters")

    if body.kind in TEXT_KINDS and not body.text.strip():
        raise HTTPException(status_code=400, detail=f"kind '{body.kind}' needs a text")

    if body.kind in IMAGE_KINDS:
        if not body.image:
            raise HTTPException(status_code=400, detail=f"kind '{body.kind}' needs an image")
        if _resolve_image(body.image) is None:
            raise HTTPException(status_code=404, detail=f"image '{body.image}' not found")

    if isinstance(body.color, str):
        rgb = palette.get(body.color.lower())
        if rgb is None:
            raise HTTPException(
                status_code=400,
                detail=f"unknown colour '{body.color}' — use an RGB triple or one of: {', '.join(sorted(palette))}",
            )
    else:
        rgb = body.color

    return DisplayConfig(
        kind=body.kind,
        text=body.text,
        image=body.image,
        font=body.font,
        duration=body.duration,
        hold=body.hold,
        color=tuple(rgb),
    )


def _record(tracker: EventTracker, config: DisplayConfig) -> None:
    from led_catcher.web.events import LedEvent

    tracker.record(
        LedEvent(
            timestamp=datetime.now().strftime("%H:%M:%S"),
            severity=config.kind,
            system=API_SYSTEM,
            title=config.text or config.image or config.kind,
            author=API_SYSTEM,
            kind=config.kind,
            message=config.text,
            color=config.color,
            duration=config.duration,
            hold=config.hold,
            text=config.text,
            font=config.font,
            image=config.image,
        )
    )
