"""Display modes for the LED matrix.

Routes DisplayConfig to the correct rendering function:
static, text (scroll), ticker, image, gif.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from PIL import Image

logger = logging.getLogger(__name__)

# Base paths for assets
FONTS_DIR = Path(__file__).parent.parent.parent.parent / "fonts"
VISUAL_AID_DIR = Path(__file__).parent.parent.parent.parent / "visual_aid"


def display_event(matrix, config) -> None:
    """Route a DisplayConfig to the correct display mode."""
    kind = config.kind.lower()
    handlers = {
        "static": _static_text,
        "text": _scroll_text,
        "ticker": _ticker_text,
        "image": _show_image,
        "gif": _show_gif,
    }
    handler = handlers.get(kind)
    if handler is None:
        logger.warning("unknown display kind '%s', falling back to static text", kind)
        handler = _static_text

    handler(matrix, config)


def _static_text(matrix, config) -> None:
    """Display centered static text for the configured duration."""
    text = config.text or "---"
    color = config.color
    font_path = _resolve_font(config.font)
    duration = config.duration

    matrix.clear()

    # Center vertically at y=36 (rough center for 64px height)
    matrix.draw_text(font_path, 2, 36, color, text)
    matrix.swap()

    time.sleep(duration)
    matrix.clear()
    matrix.swap()


def _scroll_text(matrix, config, loops: int = 1) -> None:
    """Scroll text from right to left across the matrix."""
    text = config.text or "---"
    color = config.color
    font_path = _resolve_font(config.font)

    # Approximate text width (6px per char for BDF fonts)
    text_width = len(text) * 6
    start_x = 64
    end_x = -text_width

    for _ in range(loops):
        x = start_x
        while x > end_x:
            matrix.clear()
            matrix.draw_text(font_path, x, 36, color, text)
            matrix.swap()
            time.sleep(0.03)  # ~30fps
            x -= 1

    matrix.clear()
    matrix.swap()


def _ticker_text(matrix, config) -> None:
    """Ticker mode — scroll text multiple times."""
    _scroll_text(matrix, config, loops=3)


def _show_image(matrix, config) -> None:
    """Display a static image scaled to 64x64."""
    image_path = _resolve_image(config.image)
    if image_path is None:
        logger.warning("image not found: %s", config.image)
        _static_text(matrix, config)
        return

    img = Image.open(image_path).convert("RGB").resize((64, 64), Image.LANCZOS)
    matrix.clear()
    matrix.show_image(img)
    matrix.swap()

    time.sleep(config.duration)
    matrix.clear()
    matrix.swap()


def _show_gif(matrix, config) -> None:
    """Play an animated GIF on the matrix."""
    image_path = _resolve_image(config.image)
    if image_path is None:
        logger.warning("gif not found: %s", config.image)
        _static_text(matrix, config)
        return

    gif = Image.open(image_path)
    if not getattr(gif, "is_animated", False):
        # Static image, just show it
        _show_image(matrix, config)
        return

    start = time.monotonic()
    while time.monotonic() - start < config.duration:
        for frame_idx in range(gif.n_frames):
            if time.monotonic() - start >= config.duration:
                break
            gif.seek(frame_idx)
            frame = gif.convert("RGB").resize((64, 64), Image.LANCZOS)
            matrix.show_image(frame)
            matrix.swap()
            # Use GIF frame duration or default to 50ms
            frame_duration = gif.info.get("duration", 50) / 1000.0
            time.sleep(max(frame_duration, 0.02))

    matrix.clear()
    matrix.swap()


def _resolve_font(font_name: str) -> str:
    """Resolve font name to full path."""
    path = FONTS_DIR / font_name
    if path.exists():
        return str(path)
    # Fallback: try absolute path
    if Path(font_name).exists():
        return font_name
    logger.debug("font not found: %s, using path as-is", font_name)
    return font_name


def _resolve_image(image_name: str) -> Path | None:
    """Resolve image name to full path."""
    if not image_name:
        return None
    path = VISUAL_AID_DIR / image_name
    if path.exists():
        return path
    # Try absolute path
    abs_path = Path(image_name)
    if abs_path.exists():
        return abs_path
    return None
