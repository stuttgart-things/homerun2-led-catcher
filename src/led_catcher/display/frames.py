"""Panel-ready image frames, prepared once instead of once per loop.

A GIF used to be decoded, converted to RGB and LANCZOS-resized inside the
playback loop, so every repeat paid the full cost again — on a Pi 3B+ enough
that the wait afterwards came on top of it and the GIF played slower than its
own frame timing, unevenly. Here the frames are prepared once, cached per file
and panel size, and the loop only hands them to the panel.

Scaling keeps the aspect ratio. `resize((64, 64))` stretched anything that was
not square; a non-square source is now fitted and centered, with the unused
rows or columns left dark.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageOps

logger = logging.getLogger(__name__)

# Distinct animations kept prepared. A 64x64 RGB frame is 12KB, so a long GIF
# is a megabyte or two and this is the ceiling on that — a profile cycling
# through more GIFs than this re-decodes, which is slower but not unbounded.
ANIMATION_CACHE_SIZE = 4

PREVIEW_CACHE_SIZE = 2
"""Animations kept prepared for the big-screen view (/wall), in a cache of
their own: a wall-sized GIF must not evict the frames the panel is playing."""

DEFAULT_FRAME_DELAY = 0.05
"""Delay for a frame that declares none — what the old code assumed for all of them."""

MIN_FRAME_DELAY = 0.01
"""Floor on a frame delay, so a GIF claiming 0ms cannot spin the panel flat out."""


@dataclass(frozen=True)
class Animation:
    """Frames already converted and scaled for the panel, with their own delays."""

    path: str
    frames: tuple[Image.Image, ...]
    delays: tuple[float, ...]
    """Seconds to leave each frame up, positionally matched to `frames`."""

    def __len__(self) -> int:
        return len(self.frames)

    @property
    def animated(self) -> bool:
        return len(self.frames) > 1

    @property
    def loop_duration(self) -> float:
        """Seconds one pass through every frame is meant to take."""
        return sum(self.delays)


def fit_to_panel(image: Image.Image, width: int, height: int) -> Image.Image:
    """Scale `image` to fill a `width` x `height` frame without distorting it.

    The image is scaled until it touches two opposite edges and centered; the
    remaining rows or columns stay dark. `resize((width, height))` would have
    stretched it to fit instead.
    """
    fitted = ImageOps.contain(image.convert("RGB"), (width, height), Image.LANCZOS)
    if fitted.size == (width, height):
        return fitted

    frame = Image.new("RGB", (width, height), (0, 0, 0))
    frame.paste(fitted, ((width - fitted.width) // 2, (height - fitted.height) // 2))
    return frame


def _frame_delay(info: dict) -> float:
    """The delay a GIF frame asks for, in seconds."""
    declared = info.get("duration")
    if not declared:
        # 0 and absent both mean "as fast as you can", which no panel should
        # take literally.
        return DEFAULT_FRAME_DELAY
    return max(declared / 1000.0, MIN_FRAME_DELAY)


@lru_cache(maxsize=ANIMATION_CACHE_SIZE)
def _prepare(path: str, width: int, height: int, fingerprint: tuple[int, int]) -> Animation:
    """Decode and scale every frame. `fingerprint` re-decodes an edited file."""
    return _decode(path, width, height)


@lru_cache(maxsize=PREVIEW_CACHE_SIZE)
def _prepare_preview(path: str, width: int, height: int, fingerprint: tuple[int, int]) -> Animation:
    return _decode(path, width, height, crisp=True)


def _blown_up(image: Image.Image, width: int, height: int) -> Image.Image:
    """`image` enlarged by the largest whole factor that fits, pixel for pixel.

    A 64x64 GIF made for the panel, smoothed up to a 144-row grid, is a blur;
    enlarged 2x first, it keeps its pixels and only the remainder is smoothed.
    """
    factor = min(width // image.width, height // image.height)
    if factor < 2:
        return image
    return image.convert("RGB").resize((image.width * factor, image.height * factor), Image.NEAREST)


def _decode(path: str, width: int, height: int, crisp: bool = False) -> Animation:
    frames: list[Image.Image] = []
    delays: list[float] = []

    # Closed on the way out — the old code left the file handle open for the
    # life of the process, one per GIF displayed.
    with Image.open(path) as source:
        frame_count = getattr(source, "n_frames", 1)
        for index in range(frame_count):
            source.seek(index)
            frame = _blown_up(source, width, height) if crisp else source
            frames.append(fit_to_panel(frame, width, height))
            # Pillow refreshes info on seek, so this is the frame's own delay
            # rather than the first frame's applied to all of them.
            delays.append(_frame_delay(source.info))

    logger.debug("prepared %d frame(s) of %s for %dx%d", len(frames), path, width, height)
    return Animation(path=path, frames=tuple(frames), delays=tuple(delays))


def load_animation(path: Path | str, width: int, height: int, *, preview: bool = False) -> Animation | None:
    """Frames of `path`, scaled for a `width` x `height` panel. None if unreadable.

    Never raises: an image the display cannot decode is worth a log line and a
    fallback, not taking the consumer down. With `preview` the frames are
    cached apart from the panel's (see PREVIEW_CACHE_SIZE) and enlarged pixel
    for pixel before they are fitted (`_blown_up`).
    """
    path = Path(path)
    prepare = _prepare_preview if preview else _prepare
    try:
        stat = path.stat()
        return prepare(str(path), width, height, (stat.st_mtime_ns, stat.st_size))
    except (OSError, ValueError) as exc:
        logger.warning("cannot prepare %s for display: %s", path, exc)
        return None


def clear_animation_cache() -> None:
    """Drop every prepared animation. For tests, and for freeing the frames."""
    _prepare.cache_clear()
    _prepare_preview.cache_clear()
