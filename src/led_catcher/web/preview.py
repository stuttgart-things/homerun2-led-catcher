"""What the simulator canvas needs to draw what the panel draws (#83).

The panel renders through rpi-rgb-led-matrix from BDF fonts and PIL-prepared
frames. The canvas used to draw its own card in a hand-made 3x5 font instead,
so it agreed with the panel on the text and nothing else. These routes hand
the browser the same inputs the panel uses:

- ``/api/preview/text`` — the glyph bitmaps for a string in a BDF font, plus
  the font box the modes centre with. Width and centring then match the panel
  to the pixel (#70).
- ``/api/preview/image/{name}`` — the frames of an image or GIF, prepared by
  the very ``load_animation`` the panel plays, so scaling, letterboxing and
  frame timing are the panel's own. ``width``/``height`` prepare them for the
  big-screen view (/wall) instead, which has more LEDs than the panel.
- ``/api/preview/score`` — the scoreboard as one 64x64 frame, drawn by the
  panel's own ``score.render``, for the big-screen view to scale up.
"""

from __future__ import annotations

import base64
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from PIL import Image

from led_catcher.display.bdf import load_glyphs, load_metrics
from led_catcher.display.frames import load_animation
from led_catcher.display.modes import _resolve_font, _resolve_image, is_bare_name

PANEL_WIDTH = 64
PANEL_HEIGHT = 64

# The largest grid the big-screen view asks for: 384x216 is 16:9 at 5px a LED
# on a 1080p screen. A frame is width x height x 3 bytes, base64 on top.
MAX_PREVIEW_WIDTH = 384
MAX_PREVIEW_HEIGHT = 216

# A preview request draws one display's text; anything longer is not one.
MAX_PREVIEW_TEXT = 1024


def create_preview_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/preview/text")
    async def preview_text(
        font: str = Query(..., max_length=128),
        text: str = Query("", max_length=MAX_PREVIEW_TEXT),
    ) -> dict:
        if not is_bare_name(font):
            raise HTTPException(status_code=400, detail="font must be a file name, not a path")
        # Resolved exactly as the panel resolves it, fallback font included.
        path = _resolve_font(font)
        metrics = load_metrics(path)
        glyphs = load_glyphs(path)
        wanted = {ord(char) for char in text}
        return {
            "font": Path(path).name,
            "height": metrics.height,
            "baseline": metrics.baseline,
            "width": metrics.text_width(text),
            # [advance, width, height, x_offset, y_offset, rows]
            "glyphs": {
                str(cp): [g.advance, g.width, g.height, g.x_offset, g.y_offset, list(g.rows)]
                for cp in sorted(wanted)
                if (g := glyphs.get(cp)) is not None
            },
        }

    @router.get("/api/preview/image/{name}")
    async def preview_image(
        name: str,
        width: int = Query(PANEL_WIDTH, ge=8, le=MAX_PREVIEW_WIDTH),
        height: int = Query(PANEL_HEIGHT, ge=8, le=MAX_PREVIEW_HEIGHT),
    ) -> dict:
        if not is_bare_name(name):
            raise HTTPException(status_code=400, detail="image must be a file name, not a path")
        path = _resolve_image(name)
        panel_size = (width, height) == (PANEL_WIDTH, PANEL_HEIGHT)
        animation = load_animation(path, width, height, preview=not panel_size) if path is not None else None
        if animation is None:
            # The panel falls back to static text here; the canvas does the same.
            raise HTTPException(status_code=404, detail=f"image '{name}' not found or unreadable")
        return _frames_json(width, height, animation.frames, animation.delays)

    @router.get("/api/preview/score")
    async def preview_score(text: str = Query(..., max_length=MAX_PREVIEW_TEXT)) -> dict:
        from led_catcher.display.offscreen import ImageMatrix
        from led_catcher.display.score import parse_score, render

        score = parse_score(text)
        if score is None:
            # The panel shows the text instead; so does the canvas.
            raise HTTPException(status_code=404, detail="not a score")
        matrix = ImageMatrix(PANEL_WIDTH, PANEL_HEIGHT)
        render(matrix, score)
        return _frames_json(PANEL_WIDTH, PANEL_HEIGHT, (matrix.image,), (0.0,))

    return router


def _frames_json(width: int, height: int, frames: tuple[Image.Image, ...], delays) -> dict:
    return {
        "width": width,
        "height": height,
        "animated": len(frames) > 1,
        "delays": list(delays),
        # Raw RGB, row-major, 3 bytes a pixel.
        "frames": [base64.b64encode(frame.tobytes()).decode("ascii") for frame in frames],
    }
