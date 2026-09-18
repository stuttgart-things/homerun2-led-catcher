"""A BDF font is parsed once per path, not once per draw (#69).

`graphics.Font().LoadFont()` reads and parses the BDF file from disk. A scroll
calls `draw_text` once per frame at a 30 ms step, so loading per call meant
~30 parses a second for as long as the scroll ran.

rgbmatrix is not installed off a Raspberry Pi, so `graphics` is replaced with a
double that counts the loads. That is the whole point of the test: the count is
what matters, and it is not observable on hardware either.
"""

from __future__ import annotations

import pytest

import led_catcher.display.matrix as matrix_module
from led_catcher.display import MatrixDisplay
from led_catcher.display.matrix import clear_font_cache


class FakeFont:
    """Stands in for graphics.Font — records the paths it was asked to load."""

    def __init__(self, loads: list[str]) -> None:
        self._loads = loads

    def LoadFont(self, path: str) -> None:  # noqa: N802 — mirrors the C++ binding
        self._loads.append(path)


class FakeGraphics:
    """Stands in for the rgbmatrix.graphics module."""

    def __init__(self) -> None:
        self.loads: list[str] = []
        self.drawn: list[tuple[int, int, str]] = []

    def Font(self) -> FakeFont:  # noqa: N802 — mirrors the C++ binding
        return FakeFont(self.loads)

    def Color(self, r: int, g: int, b: int) -> tuple[int, int, int]:  # noqa: N802
        return (r, g, b)

    def DrawText(self, canvas, font, x, y, color, text) -> int:  # noqa: N802
        self.drawn.append((x, y, text))
        return len(text) * 6


@pytest.fixture
def display_with_fake_graphics(monkeypatch):
    """A MatrixDisplay that believes it has hardware, drawing onto a double."""
    fake = FakeGraphics()
    monkeypatch.setattr(matrix_module, "graphics", fake, raising=False)

    display = MatrixDisplay()
    display._matrix = object()
    display._canvas = object()

    clear_font_cache()
    try:
        yield display, fake
    finally:
        # The cache outlives the monkeypatch, so a FakeFont left in it would
        # leak into the next test as a font that draws nothing.
        clear_font_cache()


def test_repeated_draws_with_one_font_load_it_once(display_with_fake_graphics):
    display, fake = display_with_fake_graphics
    font = "/fonts/6x10.bdf"

    for x in range(40):
        display.draw_text(font, x, 36, (255, 0, 0), "scrolling")

    assert fake.loads == [font], f"expected one load for {len(fake.drawn)} draws, got {len(fake.loads)}"
    assert len(fake.drawn) == 40


def test_each_distinct_font_is_loaded_once(display_with_fake_graphics):
    display, fake = display_with_fake_graphics

    for _ in range(5):
        display.draw_text("/fonts/6x10.bdf", 0, 36, (255, 0, 0), "a")
        display.draw_text("/fonts/7x13.bdf", 0, 36, (255, 0, 0), "b")

    assert sorted(fake.loads) == ["/fonts/6x10.bdf", "/fonts/7x13.bdf"]


def test_clearing_the_cache_forces_a_reload(display_with_fake_graphics):
    display, fake = display_with_fake_graphics

    display.draw_text("/fonts/6x10.bdf", 0, 36, (255, 0, 0), "a")
    clear_font_cache()
    display.draw_text("/fonts/6x10.bdf", 0, 36, (255, 0, 0), "a")

    assert fake.loads == ["/fonts/6x10.bdf", "/fonts/6x10.bdf"]


def test_the_cache_is_bounded():
    from led_catcher.display.matrix import FONT_CACHE_SIZE, _load_font

    assert _load_font.cache_info().maxsize == FONT_CACHE_SIZE
    assert FONT_CACHE_SIZE > 0
