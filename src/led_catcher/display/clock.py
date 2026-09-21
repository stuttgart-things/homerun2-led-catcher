"""The idle screen: a clock the panel shows whenever nothing else is on it (#115).

The display worker runs it between displays. Anything submitted interrupts it
at once, and it comes back by itself when that display is done, so the panel
is only dark when the idle screen is switched off.

The simulator canvas draws the same layout from the same BDF glyphs
(templates/index.html, ``idleClock``). Change one, change the other.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from led_catcher.display.bdf import load_metrics

IDLE_MODES = ("off", "clock")

TIME_FONT = "10x20.bdf"
DATE_FONT = "6x10.bdf"
# Rows between the time's font box and the weekday's, and the weekday's and the date's.
TIME_GAP = 0
DATE_GAP = 1
# Weekday and date are drawn at this share of the time's colour, so the time reads first.
DATE_DIM = 0.6

# English and fixed, whatever the locale: the canvas uses the same names.
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")

# The colon and the period are set as pixels, not taken from the fonts. The
# 10x20 colon sits on the baseline, a good two rows below the middle of the
# digits; the 6x10 period is a 3x3 plus that reads as bold next to the digits.
# Offsets are from the character cell's left edge and the line's baseline.
# The colon: two 3x2 dots in digit rows 6-7 and 11-12 of the 10x20 box, three
# rows of space above, between and below, as the digits span rows 3-15.
COLON_DOTS = tuple((x, y) for y in (-10, -9, -5, -4) for x in (4, 5, 6))
# The period: one pixel on the digits' bottom row, in the middle of its cell.
PERIOD_DOTS = ((2, -1),)


@dataclass(frozen=True)
class IdleScreen:
    """What the panel shows when it has nothing else to show."""

    mode: str = "clock"
    color: tuple[int, int, int] = (0, 100, 255)


@dataclass(frozen=True)
class ClockLine:
    font: str
    x: int
    baseline: int
    text: str
    """What DrawText gets: colon and periods already replaced by spaces."""
    dim: bool


@dataclass(frozen=True)
class ClockFace:
    """One second of the clock: the lines and the pixels set by hand."""

    lines: tuple[ClockLine, ...]
    dots: tuple[tuple[int, int, bool], ...]
    """(x, y, dim) for the colon and the periods."""


def clock_texts(now: float) -> tuple[str, str, str]:
    """The time, weekday and date for ``now`` (epoch seconds), in local time.

    The colon shows on even seconds only. It is set as pixels over a space, so
    the digits do not move when it blinks.
    """
    local = time.localtime(now)
    colon = ":" if int(now) % 2 == 0 else " "
    return (
        f"{local.tm_hour:02d}{colon}{local.tm_min:02d}",
        WEEKDAYS[local.tm_wday],
        f"{local.tm_mday:02d}.{local.tm_mon:02d}.{local.tm_year}",
    )


def clock_face(now: float, width: int, height: int, time_font: str, date_font: str) -> ClockFace:
    """Lay the clock out on a ``width`` x ``height`` panel.

    Every line is centred horizontally. The font boxes plus the gaps are
    centred vertically, the way ``baseline_for_centered_text`` centres one.
    """
    time_text, weekday, date_text = clock_texts(now)
    time_metrics = load_metrics(time_font)
    date_metrics = load_metrics(date_font)
    block = time_metrics.height + TIME_GAP + date_metrics.height + DATE_GAP + date_metrics.height
    top = max(0, (height - block) // 2)

    rows = [
        (time_font, time_metrics, top, time_text, False, ":", COLON_DOTS),
        (date_font, date_metrics, top + time_metrics.height + TIME_GAP, weekday, True, None, ()),
        (
            date_font,
            date_metrics,
            top + time_metrics.height + TIME_GAP + date_metrics.height + DATE_GAP,
            date_text,
            True,
            ".",
            PERIOD_DOTS,
        ),
    ]
    lines: list[ClockLine] = []
    dots: list[tuple[int, int, bool]] = []
    for font, metrics, box_top, text, dim, mark, mark_dots in rows:
        x = max(0, (width - metrics.text_width(text)) // 2)
        baseline = box_top + metrics.baseline
        if mark:
            for index, char in enumerate(text):
                if char == mark:
                    cell = x + metrics.text_width(text[:index])
                    dots.extend((cell + dx, baseline + dy, dim) for dx, dy in mark_dots)
            text = text.replace(mark, " ")
        lines.append(ClockLine(font=font, x=x, baseline=baseline, text=text, dim=dim))
    return ClockFace(lines=tuple(lines), dots=tuple(dots))


def dimmed(color: tuple[int, int, int], factor: float = DATE_DIM) -> tuple[int, int, int]:
    return tuple(int(c * factor) for c in color)  # type: ignore[return-value]


def draw_clock(matrix, idle: IdleScreen, now: float) -> ClockFace:
    """Draw one frame of the clock and put it on the panel."""
    from led_catcher.display.modes import _resolve_font

    face = clock_face(now, matrix.width, matrix.height, _resolve_font(TIME_FONT), _resolve_font(DATE_FONT))
    dim = dimmed(idle.color)
    matrix.clear()
    for line in face.lines:
        matrix.draw_text(line.font, line.x, line.baseline, dim if line.dim else idle.color, line.text)
    for x, y, is_dim in face.dots:
        if 0 <= x < matrix.width and 0 <= y < matrix.height:
            matrix.set_pixel(x, y, *(dim if is_dim else idle.color))
    matrix.swap()
    return face
