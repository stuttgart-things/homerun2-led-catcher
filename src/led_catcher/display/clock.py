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
# Rows between the time's font box and the date's.
LINE_GAP = 2
# The date is drawn at this share of the time's colour, so the time reads first.
DATE_DIM = 0.6

# English and fixed, whatever the locale: the canvas uses the same names.
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


@dataclass(frozen=True)
class IdleScreen:
    """What the panel shows when it has nothing else to show."""

    mode: str = "clock"
    color: tuple[int, int, int] = (0, 100, 255)


@dataclass(frozen=True)
class ClockFace:
    """One second of the clock: what is drawn, and where."""

    time_text: str
    date_text: str
    time_x: int
    time_baseline: int
    date_x: int
    date_baseline: int


def clock_texts(now: float) -> tuple[str, str]:
    """The time and date lines for ``now`` (epoch seconds), in local time.

    The colon is drawn on even seconds only. A space has the same advance in
    these monospaced fonts, so the digits do not move when it blinks.
    """
    local = time.localtime(now)
    colon = ":" if int(now) % 2 == 0 else " "
    return (
        f"{local.tm_hour:02d}{colon}{local.tm_min:02d}",
        f"{WEEKDAYS[local.tm_wday]} {local.tm_mday:02d}.{local.tm_mon:02d}",
    )


def clock_face(now: float, width: int, height: int, time_font: str, date_font: str) -> ClockFace:
    """Lay the clock out on a ``width`` x ``height`` panel.

    Both lines are centred horizontally. The two font boxes plus the gap are
    centred vertically, the way ``baseline_for_centered_text`` centres one.
    """
    time_text, date_text = clock_texts(now)
    time_metrics = load_metrics(time_font)
    date_metrics = load_metrics(date_font)
    block = time_metrics.height + LINE_GAP + date_metrics.height
    top = max(0, (height - block) // 2)
    return ClockFace(
        time_text=time_text,
        date_text=date_text,
        time_x=max(0, (width - time_metrics.text_width(time_text)) // 2),
        time_baseline=top + time_metrics.baseline,
        date_x=max(0, (width - date_metrics.text_width(date_text)) // 2),
        date_baseline=top + time_metrics.height + LINE_GAP + date_metrics.baseline,
    )


def dimmed(color: tuple[int, int, int], factor: float = DATE_DIM) -> tuple[int, int, int]:
    return tuple(int(c * factor) for c in color)  # type: ignore[return-value]


def draw_clock(matrix, idle: IdleScreen, now: float) -> ClockFace:
    """Draw one frame of the clock and put it on the panel."""
    from led_catcher.display.modes import _resolve_font

    time_font = _resolve_font(TIME_FONT)
    date_font = _resolve_font(DATE_FONT)
    face = clock_face(now, matrix.width, matrix.height, time_font, date_font)
    matrix.clear()
    matrix.draw_text(time_font, face.time_x, face.time_baseline, idle.color, face.time_text)
    matrix.draw_text(date_font, face.date_x, face.date_baseline, dimmed(idle.color), face.date_text)
    matrix.swap()
    return face
