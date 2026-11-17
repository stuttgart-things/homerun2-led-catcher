"""More faces for the idle screen: clock styles and colour modes (#162).

The ``classic`` clock (display/clock.py) blinks its colon every second and is
drawn by the simulator canvases themselves. Every other face here changes once
a minute at most and is drawn only on the server: the panel puts the frame up
itself, and the canvases fetch the same frame from ``/api/preview/clock``, so
there is one drawing of each face, not three.

Which style and which colour a moment gets is a function of the wall clock
alone. ``rotate`` and ``random`` pick per slot of ``interval`` seconds from a
generator seeded with the slot's number, so the real panel and every simulator
of the same configuration show the same face at the same time, without talking
to each other.
"""

from __future__ import annotations

import colorsys
import math
import random
import time
from dataclasses import dataclass

from led_catcher.display.bdf import load_metrics

# classic first: the default, and the only face that blinks.
STYLES = ("classic", "big", "binary", "words", "analog")
# rotate: a different style every interval, from STYLES in a shuffled order.
IDLE_STYLES = (*STYLES, "rotate")
COLOR_MODES = ("fixed", "daylight", "random")

DEFAULT_INTERVAL = 300
MIN_INTERVAL = 30
MAX_INTERVAL = 86400

# What an unlit dot of the binary clock and the dial's minute marks get of the colour.
OFF_DIM = 0.12
TICK_DIM = 0.35
WORDS_DIM = 0.45

# The colour of the day: (hour, rgb), interpolated in between and wrapping at 24.
DAYLIGHT = (
    (0.0, (40, 50, 200)),  # night: deep blue
    (5.0, (100, 40, 190)),  # before dawn: violet
    (7.0, (255, 120, 30)),  # sunrise: orange
    (10.0, (255, 200, 60)),  # morning: warm yellow
    (13.0, (110, 210, 255)),  # midday: sky
    (17.0, (255, 150, 40)),  # afternoon: orange
    (19.5, (230, 50, 60)),  # sunset: red
    (21.5, (120, 50, 200)),  # evening: purple
)


@dataclass(frozen=True)
class Face:
    """What the idle screen shows now, and for how long."""

    style: str
    color: tuple[int, int, int]
    until: float
    """Epoch seconds when the face next changes: the next minute or the next slot.
    The classic clock's blinking colon comes on top, every second."""


def _slot(now: float, interval: int) -> int:
    return int(now // interval)


def _rotation(slot: int) -> str:
    """The style of a ``rotate`` slot: each round plays every style once, shuffled."""
    order = list(STYLES)
    random.Random(slot // len(STYLES)).shuffle(order)
    return order[slot % len(STYLES)]


def _random_color(slot: int) -> tuple[int, int, int]:
    # A different stream than the rotation's, so style and colour do not move in step.
    rng = random.Random(slot * 7919 + 1)
    r, g, b = colorsys.hsv_to_rgb(rng.random(), rng.uniform(0.75, 1.0), 1.0)
    return (int(r * 255), int(g * 255), int(b * 255))


def daylight_color(now: float) -> tuple[int, int, int]:
    local = time.localtime(now)
    hour = local.tm_hour + local.tm_min / 60
    points = (*DAYLIGHT, (DAYLIGHT[0][0] + 24, DAYLIGHT[0][1]))
    for (h0, c0), (h1, c1) in zip(points, points[1:]):
        if h0 <= hour < h1:
            t = (hour - h0) / (h1 - h0)
            return tuple(round(a + (b - a) * t) for a, b in zip(c0, c1))  # type: ignore[return-value]
    return DAYLIGHT[0][1]


def resolve(idle, now: float) -> Face:
    """The face an idle screen shows at ``now``: style, colour, and until when."""
    interval = idle.interval
    slot = _slot(now, interval)
    style = _rotation(slot) if idle.style == "rotate" else idle.style
    if idle.color_mode == "daylight":
        color = daylight_color(now)
    elif idle.color_mode == "random":
        color = _random_color(slot)
    else:
        color = tuple(idle.color)
    next_minute = (now // 60 + 1) * 60
    until = next_minute
    if idle.style == "rotate" or idle.color_mode == "random":
        until = min(until, (slot + 1) * interval)
    return Face(style=style, color=color, until=until)  # type: ignore[arg-type]


# A clock in a playlist (#162) travels as a string among the image names, so
# the schedule, the shuffle and GET /display handle it like any image:
# "clock:<style>:<colour mode>:<r>,<g>,<b>". ':' never occurs in an image's
# bare file name, and URLs start with https.
CLOCK_ITEM_PREFIX = "clock:"


def clock_item(style: str, color_mode: str, color) -> str:
    return f"{CLOCK_ITEM_PREFIX}{style}:{color_mode}:{','.join(str(int(c)) for c in color)}"


def parse_clock_item(item: str):
    """The IdleScreen a playlist's clock item shows, or None for an image."""
    from led_catcher.display.clock import IdleScreen

    if not item.startswith(CLOCK_ITEM_PREFIX):
        return None
    try:
        style, color_mode, rgb = item[len(CLOCK_ITEM_PREFIX) :].split(":")
        color = tuple(int(c) for c in rgb.split(","))
    except ValueError:
        return None
    if style not in IDLE_STYLES or color_mode not in COLOR_MODES or len(color) != 3:
        return None
    return IdleScreen(style=style, color_mode=color_mode, color=color)


def dim(color, factor: float) -> tuple[int, int, int]:
    return tuple(int(c * factor) for c in color)  # type: ignore[return-value]


# ---- the faces, each on a 64x64 matrix ----


def _fill(matrix, x: int, y: int, w: int, h: int, color) -> None:
    for py in range(y, y + h):
        for px in range(x, x + w):
            matrix.set_pixel(px, py, *color)


def _seven(matrix, char: str, x: int, y: int, w: int, h: int, t: int, color) -> None:
    """A seven-segment digit of any size, top-left at (x, y)."""
    from led_catcher.display.score import _SEGMENTS

    on = _SEGMENTS.get(char, "")
    mid = y + (h - t) // 2
    inner = w - 2 * t
    upper = mid - (y + t)
    lower = (y + h - t) - (mid + t)
    if "a" in on:
        _fill(matrix, x + t, y, inner, t, color)
    if "g" in on:
        _fill(matrix, x + t, mid, inner, t, color)
    if "d" in on:
        _fill(matrix, x + t, y + h - t, inner, t, color)
    if "f" in on:
        _fill(matrix, x, y + t, t, upper, color)
    if "b" in on:
        _fill(matrix, x + w - t, y + t, t, upper, color)
    if "e" in on:
        _fill(matrix, x, mid + t, t, lower, color)
    if "c" in on:
        _fill(matrix, x + w - t, mid + t, t, lower, color)


BIG_W, BIG_H, BIG_T, BIG_GAP = 20, 28, 4, 4


def draw_big(matrix, color, local: time.struct_time) -> None:
    """Hours over minutes in seven-segment digits as large as the panel takes."""
    pair_w = 2 * BIG_W + BIG_GAP
    x = (matrix.width - pair_w) // 2
    top = (matrix.height - (2 * BIG_H + BIG_GAP)) // 2
    for row, value, shade in ((0, local.tm_hour, color), (1, local.tm_min, dim(color, 0.7))):
        y = top + row * (BIG_H + BIG_GAP)
        for i, char in enumerate(f"{value:02d}"):
            _seven(matrix, char, x + i * (BIG_W + BIG_GAP), y, BIG_W, BIG_H, BIG_T, shade)


BINARY_DOT = 9
BINARY_ROW = 12
# Left edges of the columns: hour tens, hour units, minute tens, minute units.
BINARY_COLS = (7, 19, 36, 48)
BINARY_BITS = (2, 4, 3, 4)


def draw_binary(matrix, color, local: time.struct_time) -> None:
    """BCD: one column per digit, the 8 on top; the time below in small figures."""
    from led_catcher.display.modes import _resolve_font

    digits = (local.tm_hour // 10, local.tm_hour % 10, local.tm_min // 10, local.tm_min % 10)
    off = dim(color, OFF_DIM)
    for x, value, bits in zip(BINARY_COLS, digits, BINARY_BITS):
        for row in range(4):
            bit = 3 - row
            if bit >= bits:
                continue
            _fill(matrix, x, 3 + row * BINARY_ROW, BINARY_DOT, BINARY_DOT, color if value >> bit & 1 else off)
    font = _resolve_font("5x7.bdf")
    text = f"{local.tm_hour:02d}:{local.tm_min:02d}"
    x = (matrix.width - load_metrics(font).text_width(text)) // 2
    matrix.draw_text(font, x, 62, dim(color, 0.6), text)


HOURS = ("ZWÖLF", "EINS", "ZWEI", "DREI", "VIER", "FÜNF", "SECHS", "SIEBEN", "ACHT", "NEUN", "ZEHN", "ELF")

# Per five minutes: the words before the hour, and whether it is the next hour.
WORDS = (
    ((), False),
    (("FÜNF NACH",), False),
    (("ZEHN NACH",), False),
    (("VIERTEL", "NACH"), False),
    (("ZWANZIG", "NACH"), False),
    (("FÜNF VOR", "HALB"), True),
    (("HALB",), True),
    (("FÜNF NACH", "HALB"), True),
    (("ZWANZIG", "VOR"), True),
    (("VIERTEL", "VOR"), True),
    (("ZEHN VOR",), True),
    (("FÜNF VOR",), True),
)


def word_lines(hour: int, minute: int) -> tuple[str, ...]:
    """The time as a German word clock says it, in lines that fit 64 columns."""
    words, next_hour = WORDS[minute // 5]
    h = (hour + (1 if next_hour else 0)) % 12
    name = HOURS[h]
    if not words:
        return ("ES IST", "EIN" if h == 1 else name, "UHR")
    return ("ES IST", *words, name)


def draw_words(matrix, color, local: time.struct_time) -> None:
    """A word clock: "ES IST VIERTEL NACH ZEHN", the minutes past in corner dots."""
    from led_catcher.display.modes import _resolve_font

    lines = word_lines(local.tm_hour, local.tm_min)
    fonts = [_resolve_font("7x13.bdf"), _resolve_font("6x10.bdf")]
    chosen = []
    for line in lines:
        font = next((f for f in fonts if load_metrics(f).text_width(line) <= matrix.width - 2), fonts[-1])
        chosen.append((font, load_metrics(font)))
    gap = 2
    block = sum(m.height for _, m in chosen) + gap * (len(chosen) - 1)
    y = (matrix.height - block) // 2
    for i, (line, (font, metrics)) in enumerate(zip(lines, chosen)):
        x = (matrix.width - metrics.text_width(line)) // 2
        matrix.draw_text(font, x, y + metrics.baseline, dim(color, WORDS_DIM) if i == 0 else color, line)
        y += metrics.height + gap
    corners = ((0, 0), (matrix.width - 2, 0), (matrix.width - 2, matrix.height - 2), (0, matrix.height - 2))
    for x, y in corners[: local.tm_min % 5]:
        _fill(matrix, x, y, 2, 2, color)


def _line(matrix, x0: float, y0: float, x1: float, y1: float, color, width: int = 1) -> None:
    steps = int(max(abs(x1 - x0), abs(y1 - y0)) * 2) + 1
    for i in range(steps + 1):
        t = i / steps
        x = round(x0 + (x1 - x0) * t)
        y = round(y0 + (y1 - y0) * t)
        for dx in range(width):
            for dy in range(width):
                matrix.set_pixel(x + dx - width // 2, y + dy - width // 2, *color)


def draw_analog(matrix, color, local: time.struct_time) -> None:
    """A dial: twelve marks, the quarters longer, hour and minute hands."""
    cx = (matrix.width - 1) / 2
    cy = (matrix.height - 1) / 2
    radius = min(matrix.width, matrix.height) / 2 - 1
    tick = dim(color, TICK_DIM)
    for mark in range(12):
        angle = math.radians(mark * 30)
        inner = radius - (5 if mark % 3 == 0 else 2)
        shade = color if mark % 3 == 0 else tick
        _line(
            matrix,
            cx + inner * math.sin(angle),
            cy - inner * math.cos(angle),
            cx + radius * math.sin(angle),
            cy - radius * math.cos(angle),
            shade,
        )
    minute = math.radians(local.tm_min * 6)
    hour = math.radians((local.tm_hour % 12) * 30 + local.tm_min * 0.5)
    _line(matrix, cx, cy, cx + 0.55 * radius * math.sin(hour), cy - 0.55 * radius * math.cos(hour), color, width=3)
    _line(matrix, cx, cy, cx + 0.85 * radius * math.sin(minute), cy - 0.85 * radius * math.cos(minute), color, width=2)
    _fill(matrix, round(cx) - 1, round(cy) - 1, 3, 3, dim(color, 0.6))


DRAW = {"big": draw_big, "binary": draw_binary, "words": draw_words, "analog": draw_analog}


def render(style: str, color, now: float, width: int = 64, height: int = 64):
    """One frame of a face other than ``classic``, as a Pillow image."""
    from led_catcher.display.offscreen import ImageMatrix

    matrix = ImageMatrix(width, height)
    DRAW[style](matrix, tuple(color), time.localtime(now))
    return matrix.image
