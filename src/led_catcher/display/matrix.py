"""LED matrix display abstraction.

Wraps rpi-rgb-led-matrix when available, falls back to a no-op
stub when running without hardware (dev machines, CI, web-only mode).
"""

from __future__ import annotations

import logging
from functools import lru_cache

from PIL import Image

from led_catcher.config.settings import PanelConfig, load_panel_config
from led_catcher.display.bdf import load_metrics

logger = logging.getLogger(__name__)

# Try importing rgbmatrix — optional dependency (only on Raspberry Pi)
try:
    from rgbmatrix import RGBMatrix, RGBMatrixOptions, graphics  # type: ignore[import-untyped]

    HAS_RGBMATRIX = True
except ImportError:
    HAS_RGBMATRIX = False
    logger.info("rgbmatrix not available — running in software-only mode")


# Distinct fonts a running process is expected to touch. A profile names a
# handful; the bound is here so a pathological profile cannot grow the cache
# without limit.
FONT_CACHE_SIZE = 32


@lru_cache(maxsize=FONT_CACHE_SIZE)
def _load_font(font_path: str):
    """Load a BDF font, once per path.

    `graphics.Font().LoadFont()` re-reads and re-parses the BDF file from disk
    on every call. A scroll draws a frame every 30 ms, so loading per draw
    parsed the file ~30x/s for the length of the scroll — the parse time landed
    on top of every frame's budget and made the scroll speed uneven on a
    Pi 3B+ (#69).
    """
    font = graphics.Font()
    font.LoadFont(font_path)
    logger.debug("loaded BDF font %s", font_path)
    return font


def clear_font_cache() -> None:
    """Drop every cached font.

    For tests, and for a process whose font directory changed underneath it.
    """
    _load_font.cache_clear()


class MatrixDisplay:
    """Abstraction over the physical LED matrix."""

    def __init__(self, panel: PanelConfig | None = None) -> None:
        self._panel = panel if panel is not None else load_panel_config()
        self._matrix = None
        self._canvas = None
        if HAS_RGBMATRIX:
            self._init_hardware()

    def _init_hardware(self) -> None:
        panel = self._panel
        options = RGBMatrixOptions()
        options.rows = panel.rows
        options.cols = panel.cols
        options.chain_length = 1
        options.parallel = 1
        options.hardware_mapping = panel.hardware_mapping
        options.brightness = panel.brightness
        options.drop_privileges = True

        # Only set what was actually configured. The library picks these per
        # board, and writing a value here would replace a working default with
        # this project's guess.
        if panel.gpio_slowdown is not None:
            options.gpio_slowdown = panel.gpio_slowdown
        if panel.pwm_bits is not None:
            options.pwm_bits = panel.pwm_bits
        if panel.panel_type:
            options.panel_type = panel.panel_type

        # Logged before RGBMatrix() rather than after: an unknown
        # hardware_mapping makes the library abort the process here, and this
        # line is what says which one it was handed.
        logger.info("initializing RGB LED matrix (%s)", panel.describe())
        self._matrix = RGBMatrix(options=options)
        self._canvas = self._matrix.CreateFrameCanvas()
        logger.info("RGB LED matrix initialized (%s)", panel.describe())

    @property
    def available(self) -> bool:
        return self._matrix is not None

    @property
    def panel(self) -> PanelConfig:
        """The options this display was built with."""
        return self._panel

    @property
    def width(self) -> int:
        """Panel width in pixels — the configured geometry when there is no hardware."""
        return self._matrix.width if self._matrix else self._panel.cols

    @property
    def height(self) -> int:
        """Panel height in pixels — the configured geometry when there is no hardware."""
        return self._matrix.height if self._matrix else self._panel.rows

    def show(self, config) -> None:
        """Display content based on a DisplayConfig."""
        from led_catcher.display.modes import display_event

        display_event(self, config)

    def set_pixel(self, x: int, y: int, r: int, g: int, b: int) -> None:
        if self._canvas:
            self._canvas.SetPixel(x, y, r, g, b)

    def draw_text(self, font_path: str, x: int, y: int, color: tuple[int, int, int], text: str) -> int:
        """Draw text at baseline y and return the width drawn, in pixels."""
        if not self._matrix:
            logger.debug("draw_text (no-op): '%s' at (%d,%d) color=%s", text, x, y, color)
            # Measured from the BDF file, so the width off hardware is the
            # width on it — a scroll ends in the same place either way (#70).
            return load_metrics(font_path).text_width(text)

        font = _load_font(font_path)
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


def get_display(panel: PanelConfig | None = None) -> MatrixDisplay:
    """The process-wide display, built on first use.

    `panel` is only read the first time; afterwards the existing display is
    returned whatever is passed, because there is one panel and reopening it
    under a second set of options is not a thing the hardware allows.
    """
    global _display
    if _display is None:
        _display = MatrixDisplay(panel)
    return _display
