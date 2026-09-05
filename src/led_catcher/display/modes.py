"""Display modes for the LED matrix.

Routes DisplayConfig to the correct rendering function:
static, text (scroll), ticker, image, gif.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from PIL import Image

logger = logging.getLogger(__name__)

# Font used when a profile references a font that cannot be found.
DEFAULT_FONT = "6x10.bdf"

# src/led_catcher — the installed package itself
_PACKAGE_DIR = Path(__file__).resolve().parent.parent
# Repo root when running from the src/ layout (editable install or checkout)
_REPO_ROOT = _PACKAGE_DIR.parent.parent


def _asset_dirs(env_var: str, name: str) -> list[Path]:
    """Candidate directories for an asset kind, in lookup order.

    Read at call time so the environment can be changed after import
    (tests, and deployments that set the env in a systemd unit).
    """
    dirs: list[Path] = []
    override = os.environ.get(env_var, "").strip()
    if override:
        dirs.append(Path(override))
    dirs.append(_REPO_ROOT / name)  # checkout / editable install
    dirs.append(_PACKAGE_DIR / name)  # assets shipped inside the package
    dirs.append(Path("/app") / name)  # container layout
    dirs.append(Path.cwd() / name)  # process working directory
    return dirs


def fonts_dirs() -> list[Path]:
    """Directories searched for BDF fonts. Override with $FONTS_DIR."""
    return _asset_dirs("FONTS_DIR", "fonts")


def visual_aid_dirs() -> list[Path]:
    """Directories searched for images and GIFs. Override with $VISUAL_AID_DIR."""
    return _asset_dirs("VISUAL_AID_DIR", "visual_aid")


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


def _search(dirs: list[Path], name: str) -> Path | None:
    """Find `name` in `dirs`, or as an absolute/relative path of its own."""
    for directory in dirs:
        candidate = directory / name
        if candidate.is_file():
            return candidate
    direct = Path(name)
    if direct.is_file():
        return direct
    return None


def _resolve_font(font_name: str) -> str:
    """Resolve a font name to a full path, falling back to the default font.

    rpi-rgb-led-matrix aborts the process when LoadFont() gets a path that does
    not exist, so an unresolvable name must never be handed through as-is.
    """
    dirs = fonts_dirs()
    found = _search(dirs, font_name)
    if found is not None:
        return str(found)

    fallback_name = os.environ.get("LED_DEFAULT_FONT", "").strip() or DEFAULT_FONT
    fallback = _search(dirs, fallback_name)
    if fallback is not None:
        logger.warning("font '%s' not found, falling back to '%s'", font_name, fallback_name)
        return str(fallback)

    logger.error(
        "font '%s' not found and fallback '%s' is missing — searched %s",
        font_name,
        fallback_name,
        ", ".join(str(d) for d in dirs),
    )
    return font_name


def _resolve_image(image_name: str) -> Path | None:
    """Resolve an image name to a full path."""
    if not image_name:
        return None
    return _search(visual_aid_dirs(), image_name)
