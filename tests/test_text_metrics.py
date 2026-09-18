"""Text is measured in the font it is drawn in, not assumed to be 6px/char (#70).

`_scroll_text` used `len(text) * 6`, which is right for `6x10.bdf` and wrong
for every other bundled font: with `7x13.bdf` the scroll ended while the tail
was still on the panel, with `4x6.bdf` it scrolled an empty panel for a while.
`_static_text` drew at a fixed `x=2, y=36` despite promising centered text.

The widths here are checked against the fonts in `fonts/`, whose names state
their own geometry — `7x13.bdf` advances 7px per character, so five of them are
35px, and nothing about that depends on having a panel attached.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from led_catcher.display.bdf import (
    FALLBACK_ADVANCE,
    FALLBACK_BASELINE,
    FALLBACK_HEIGHT,
    clear_metrics_cache,
    load_metrics,
)
from led_catcher.display.modes import _scroll_text, _static_text
from led_catcher.profile import DisplayConfig

FONTS = Path(__file__).resolve().parent.parent / "fonts"


class FakeMatrix:
    """A 64x64 panel that records where text landed instead of lighting it."""

    def __init__(self, width: int = 64, height: int = 64) -> None:
        self.width = width
        self.height = height
        self.texts: list[tuple[int, int, str]] = []
        self.clears = 0
        self.swaps = 0

    def draw_text(self, font_path: str, x: int, y: int, color, text: str) -> int:
        self.texts.append((x, y, text))
        return load_metrics(font_path).text_width(text)

    def show_image(self, image) -> None:  # pragma: no cover - not exercised here
        pass

    def clear(self) -> None:
        self.clears += 1

    def swap(self) -> None:
        self.swaps += 1


def noop_wait(seconds: float, hold: bool = False) -> None:
    """Spend no time on the panel, so a full scroll runs in microseconds."""


@pytest.fixture(autouse=True)
def _fresh_metrics_cache():
    clear_metrics_cache()
    yield
    clear_metrics_cache()


# ── the metrics themselves ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("font", "advance", "height", "baseline"),
    [
        ("4x6.bdf", 4, 6, 5),
        ("6x10.bdf", 6, 10, 8),
        ("7x13.bdf", 7, 13, 11),
    ],
)
def test_bundled_fonts_measure_as_their_names_claim(font, advance, height, baseline):
    metrics = load_metrics(str(FONTS / font))

    assert metrics.measured
    assert metrics.height == height
    # FONTBOUNDINGBOX height + its (negative) y offset, as rpi-rgb-led-matrix reads it.
    assert metrics.baseline == baseline
    assert metrics.text_width("HELLO") == 5 * advance


def test_the_empty_string_is_zero_pixels_wide():
    assert load_metrics(str(FONTS / "6x10.bdf")).text_width("") == 0


def test_a_font_that_cannot_be_read_falls_back_to_an_estimate(caplog, tmp_path):
    missing = tmp_path / "gone.bdf"

    metrics = load_metrics(str(missing))

    assert not metrics.measured
    assert metrics.text_width("abcd") == 4 * FALLBACK_ADVANCE
    assert metrics.height == FALLBACK_HEIGHT
    assert metrics.baseline == FALLBACK_BASELINE
    assert "estimating" in caplog.text


def test_a_bdf_without_glyphs_falls_back_to_an_estimate(caplog, tmp_path):
    empty = tmp_path / "empty.bdf"
    empty.write_text("STARTFONT 2.1\nFONTBOUNDINGBOX 9 15 0 -3\nENDFONT\n")

    metrics = load_metrics(str(empty))

    assert not metrics.measured
    # The bounding box was still readable, so the geometry is not guessed.
    assert (metrics.height, metrics.baseline) == (15, 12)
    assert metrics.text_width("ab") == 2 * FALLBACK_ADVANCE
    assert "no glyph widths" in caplog.text


def test_metrics_are_parsed_once_per_path():
    path = str(FONTS / "6x10.bdf")

    load_metrics(path)
    hits_before = load_metrics.cache_info().hits
    load_metrics(path)

    assert load_metrics.cache_info().hits == hits_before + 1


def test_centering_a_font_box_on_the_panel():
    # 7x13 on 64 rows: 25 rows above the box, baseline 11 rows into it.
    assert load_metrics(str(FONTS / "7x13.bdf")).baseline_for_centered_text(64) == 36
    # A font taller than the panel is pinned to the top rather than pushed off it.
    assert load_metrics(str(FONTS / "7x13.bdf")).baseline_for_centered_text(8) == 11


# ── the scroll ───────────────────────────────────────────────────────────────


def _scroll_positions(font: str, text: str) -> list[int]:
    matrix = FakeMatrix()
    config = DisplayConfig(kind="text", text=text, duration=0.0, color=(255, 0, 0), font=font)
    _scroll_text(matrix, config, noop_wait)
    return [x for x, _, _ in matrix.texts]


@pytest.mark.parametrize(("font", "advance"), [("4x6.bdf", 4), ("6x10.bdf", 6), ("7x13.bdf", 7)])
def test_a_scroll_runs_until_the_text_has_left_the_panel(font, advance):
    text = "SCROLL ME"
    expected_width = len(text) * advance

    positions = _scroll_positions(font, text)

    assert positions[0] == 64, "a scroll starts at the right panel edge"
    # The last frame drawn is the last one still even partly on the panel; one
    # more step would put the text's right edge at x=0.
    assert positions[-1] == -expected_width + 1
    assert len(positions) == 64 + expected_width


def test_the_scroll_length_follows_the_font_rather_than_the_character_count():
    narrow = _scroll_positions("4x6.bdf", "SCROLL ME")
    wide = _scroll_positions("7x13.bdf", "SCROLL ME")

    assert len(wide) > len(narrow), (
        "the same text in a wider font has to scroll further — with the old "
        "6px/char estimate both ran for exactly the same number of frames"
    )


@pytest.mark.parametrize(("font", "baseline"), [("4x6.bdf", 34), ("6x10.bdf", 35), ("7x13.bdf", 36)])
def test_a_scroll_sits_on_a_baseline_derived_from_the_font(font, baseline):
    matrix = FakeMatrix()
    config = DisplayConfig(kind="text", text="HI", duration=0.0, color=(255, 0, 0), font=font)

    _scroll_text(matrix, config, noop_wait)

    # Every frame of the scroll, not just the first, and not the hardcoded 36
    # that only happened to suit 7x13.
    assert {y for _, y, _ in matrix.texts} == {baseline}


# ── static text ──────────────────────────────────────────────────────────────


def _render_static(font: str, text: str, width: int = 64, height: int = 64) -> FakeMatrix:
    matrix = FakeMatrix(width=width, height=height)
    config = DisplayConfig(kind="static", text=text, duration=0.0, color=(255, 0, 0), font=font)
    _static_text(matrix, config, noop_wait)
    return matrix


@pytest.mark.parametrize(("font", "advance"), [("4x6.bdf", 4), ("6x10.bdf", 6), ("7x13.bdf", 7)])
def test_static_text_is_centered_horizontally(font, advance):
    text = "OK"

    matrix = _render_static(font, text)

    ((x, _, drawn),) = matrix.texts
    assert drawn == text
    assert x == (64 - len(text) * advance) // 2


def test_static_text_that_fills_the_panel_exactly_starts_at_zero():
    # 16 characters of 4x6 is exactly 64px.
    matrix = _render_static("4x6.bdf", "X" * 16)

    assert matrix.texts[0][0] == 0


def test_static_text_too_wide_to_fit_is_clipped_and_reported(caplog):
    matrix = _render_static("7x13.bdf", "FAR TOO LONG FOR THIS PANEL")

    x, _, _ = matrix.texts[0]
    assert x == 0, "an overflowing line starts at the left edge rather than off-panel"
    assert "clipped" in caplog.text
    assert "7x13.bdf" in caplog.text


def test_static_text_follows_the_panel_it_is_given():
    # Not 64 anywhere: the geometry comes from the matrix, not from a constant.
    matrix = _render_static("6x10.bdf", "AB", width=32, height=16)

    x, y, _ = matrix.texts[0]
    assert x == (32 - 2 * 6) // 2
    assert y == (16 - 10) // 2 + 8
