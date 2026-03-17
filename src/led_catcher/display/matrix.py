"""LED matrix display abstraction.

Wraps rpi-rgb-led-matrix when available, falls back to a no-op
stub when running without hardware (dev machines, CI, web-only mode).
"""

from __future__ import annotations

import logging

from PIL import Image

logger = logging.getLogger(__name__)

# Try importing rgbmatrix — optional dependency (only on Raspberry Pi)
try:
    from rgbmatrix import RGBMatrix, RGBMatrixOptions, graphics  # type: ignore[import-untyped]

    HAS_RGBMATRIX = True
except ImportError:
    HAS_RGBMATRIX = False
    logger.info("rgbmatrix not available — running in software-only mode")


class MatrixDisplay:
    """Abstraction over the physical LED matrix."""

    def __init__(self) -> None:
        self._matrix = None
        self._canvas = None
        if HAS_RGBMATRIX:
            self._init_hardware()

    def _init_hardware(self) -> None:
        options = RGBMatrixOptions()
        options.rows = 64
        options.cols = 64
        options.chain_length = 1
        options.parallel = 1
        options.hardware_mapping = "adafruit-hat"
        options.drop_privileges = True

        self._matrix = RGBMatrix(options=options)
        self._canvas = self._matrix.CreateFrameCanvas()
        logger.info("RGB LED matrix initialized (64x64, adafruit-hat)")

    @property
    def available(self) -> bool:
        return self._matrix is not None

    def show(self, config) -> None:
        """Display content based on a DisplayConfig."""
        from led_catcher.display.modes import display_event

        display_event(self, config)

    def set_pixel(self, x: int, y: int, r: int, g: int, b: int) -> None:
        if self._canvas:
            self._canvas.SetPixel(x, y, r, g, b)

    def draw_text(self, font_path: str, x: int, y: int, color: tuple[int, int, int], text: str) -> int:
        """Draw text and return the text length in pixels."""
        if not self._matrix:
            logger.debug("draw_text (no-op): '%s' at (%d,%d) color=%s", text, x, y, color)
            return len(text) * 6  # approximate width

        font = graphics.Font()
        font.LoadFont(font_path)
        text_color = graphics.Color(color[0], color[1], color[2])
        return graphics.DrawText(self._canvas, font, x, y, text_color, text)

    def show_image(self, image: Image.Image) -> None:
        if self._matrix:
            self._canvas.SetImage(image.convert("RGB"))
        else:
            logger.debug("show_image (no-op): %s", image.size)

    def swap(self) -> None:
        if self._matrix:
            self._canvas = self._matrix.SwapOnVSync(self._canvas)

    def clear(self) -> None:
        if self._canvas:
            self._canvas.Clear()
        elif self._matrix:
            self._matrix.Clear()

    def shutdown(self) -> None:
        self.clear()
        if self._matrix:
            self.swap()
            logger.info("matrix display shut down")


# Singleton display instance
_display: MatrixDisplay | None = None


def get_display() -> MatrixDisplay:
    global _display
    if _display is None:
        _display = MatrixDisplay()
    return _display
