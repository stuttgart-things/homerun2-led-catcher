"""Scoreboard display mode for a live table tennis match.

The other modes render one line of BDF text, which is the right shape for a
notification and the wrong one for a score: a score is read from across the
room, mid-rally, by someone who was not looking for it. So the points get their
own geometry — seven-segment digits at 11x20 — and everything around them stays
in the 3x5 glyphs the simulator already uses, so the panel does not grow a
second typographic language.

Nothing here knows the rules of table tennis. Who is serving, whether this is a
set point and who has won arrive on the wire; zaehlwerk owns the match and this
module owns the pixels. See zaehlwerk ADR-0001.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

WIDTH = 64
HEIGHT = 64

# The panel is not a severity indicator, so the score does not take its colour
# from the message's severity the way the text modes do. A score that turns red
# because the pitcher happened to send ERROR would read as a warning about the
# match rather than the match itself.
COLOR_SCORE = (255, 214, 160)
COLOR_LABEL = (116, 122, 190)
COLOR_SERVE = (249, 115, 22)
COLOR_FAINT = (70, 78, 110)
COLOR_WON = (34, 197, 94)

# Row allocation. Kept as constants because the simulator draws the same layout
# and the two drifting apart is the failure that is hardest to notice.
Y_HEADER = 1
Y_DIVIDER = 7
Y_SCORE = 10
Y_NAMES = 33
Y_BANNER = 43
Y_PLAYED = 51
Y_SOURCE = 58

# Seven-segment geometry: 11 wide, 20 tall, 3px stroke.
DIGIT_W = 11
DIGIT_H = 20
DIGIT_T = 3
DIGIT_GAP = 2
# x of the last column of the left number, and the first column of the right.
LEFT_EDGE = 27
RIGHT_START = 36
COLON_X = 30

MAX_NAME = 6
MAX_PLAYED = 3

_SEGMENTS = {
    "0": "abcdef",
    "1": "bc",
    "2": "abged",
    "3": "abgcd",
    "4": "fgbc",
    "5": "afgcd",
    "6": "afgecd",
    "7": "abc",
    "8": "abcdefg",
    "9": "abcdfg",
}

# 3x5 glyphs, the same set the HTMX simulator ships.
_GLYPHS: dict[str, tuple[str, ...]] = {
    "A": ("111", "101", "111", "101", "101"),
    "B": ("110", "101", "110", "101", "110"),
    "C": ("111", "100", "100", "100", "111"),
    "D": ("110", "101", "101", "101", "110"),
    "E": ("111", "100", "110", "100", "111"),
    "F": ("111", "100", "110", "100", "100"),
    "G": ("111", "100", "101", "101", "111"),
    "H": ("101", "101", "111", "101", "101"),
    "I": ("111", "010", "010", "010", "111"),
    "J": ("001", "001", "001", "101", "111"),
    "K": ("101", "110", "100", "110", "101"),
    "L": ("100", "100", "100", "100", "111"),
    "M": ("101", "111", "111", "101", "101"),
    "N": ("101", "111", "111", "111", "101"),
    "O": ("111", "101", "101", "101", "111"),
    "P": ("111", "101", "111", "100", "100"),
    "Q": ("111", "101", "101", "111", "001"),
    "R": ("111", "101", "110", "101", "101"),
    "S": ("111", "100", "111", "001", "111"),
    "T": ("111", "010", "010", "010", "010"),
    "U": ("101", "101", "101", "101", "111"),
    "V": ("101", "101", "101", "101", "010"),
    "W": ("101", "101", "111", "111", "101"),
    "X": ("101", "101", "010", "101", "101"),
    "Y": ("101", "101", "010", "010", "010"),
    "Z": ("111", "001", "010", "100", "111"),
    "0": ("111", "101", "101", "101", "111"),
    "1": ("010", "110", "010", "010", "111"),
    "2": ("111", "001", "111", "100", "111"),
    "3": ("111", "001", "111", "001", "111"),
    "4": ("101", "101", "111", "001", "001"),
    "5": ("111", "100", "111", "001", "111"),
    "6": ("111", "100", "111", "101", "111"),
    "7": ("111", "001", "001", "001", "001"),
    "8": ("111", "101", "111", "101", "111"),
    "9": ("111", "101", "111", "001", "111"),
    " ": ("00", "00", "00", "00", "00"),
    ".": ("0", "0", "0", "0", "1"),
    ":": ("0", "1", "0", "1", "0"),
    "-": ("000", "000", "111", "000", "000"),
    "?": ("111", "001", "010", "000", "010"),
}


@dataclass
class Score:
    """One frame of a match, exactly as it arrived. No rules are applied."""

    points: tuple[int, int] = (0, 0)
    sets: tuple[int, int] = (0, 0)
    set_number: int = 1
    # None means "not stated" — the dot is simply not drawn. A scoreboard that
    # guesses who serves is worse than one that stays quiet about it.
    serving: int | None = None
    names: tuple[str, str] = ("A", "B")
    played: list[tuple[int, int]] = field(default_factory=list)
    banner: str = ""
    winner: int | None = None
    source: str = ""


def _pair(raw: str, sep: str = ":") -> tuple[int, int] | None:
    left, _, right = raw.partition(sep)
    try:
        return int(left), int(right)
    except ValueError:
        return None


def _side(raw: str) -> int | None:
    key = raw.strip().lower()
    if key in ("a", "0"):
        return 0
    if key in ("b", "1"):
        return 1
    return None


def parse_score(text: str) -> Score | None:
    """Parse the semicolon-separated payload into a [Score].

    Unknown keys are ignored so the pitcher can add fields without a lockstep
    release, and every field has a default. Only a payload that carries no
    usable points at all is rejected — the caller then falls back to static
    text rather than blanking the panel.
    """
    if not text or not text.strip():
        return None

    fields: dict[str, str] = {}
    for chunk in text.split(";"):
        key, sep, value = chunk.partition("=")
        if sep:
            fields[key.strip().lower()] = value.strip()

    points = _pair(fields.get("points", ""))
    if points is None:
        return None

    score = Score(points=points)

    sets = _pair(fields.get("sets", ""))
    if sets is not None:
        score.sets = sets

    try:
        score.set_number = int(fields.get("set", "1"))
    except ValueError:
        pass

    if "serve" in fields:
        score.serving = _side(fields["serve"])
    if "winner" in fields:
        score.winner = _side(fields["winner"])

    score.names = (
        (fields.get("a") or "A").upper()[:MAX_NAME],
        (fields.get("b") or "B").upper()[:MAX_NAME],
    )

    for chunk in fields.get("played", "").split(","):
        if not chunk.strip():
            continue
        played = _pair(chunk, "-")
        if played is not None:
            score.played.append(played)

    score.banner = fields.get("banner", "").upper()
    score.source = fields.get("source", "").upper()
    return score


# --------------------------------------------------------------------- drawing


def _fill(matrix, x: int, y: int, w: int, h: int, color: tuple[int, int, int]) -> None:
    for dy in range(h):
        for dx in range(w):
            px, py = x + dx, y + dy
            if 0 <= px < WIDTH and 0 <= py < HEIGHT:
                matrix.set_pixel(px, py, *color)


def glyph_width(char: str) -> int:
    glyph = _GLYPHS.get(char)
    return len(glyph[0]) if glyph else 3


def text_width(text: str) -> int:
    """Width in pixels of `text` in the 3x5 glyphs, including inter-glyph gaps."""
    if not text:
        return 0
    return sum(glyph_width(c) + 1 for c in text.upper()) - 1


def draw_text(matrix, text: str, x: int, y: int, color: tuple[int, int, int]) -> int:
    """Draw `text` in the 3x5 glyphs and return the x just past the last one."""
    cursor = x
    for char in text.upper():
        glyph = _GLYPHS.get(char)
        if glyph is None:
            cursor += 4
            continue
        for row, bits in enumerate(glyph):
            for col, bit in enumerate(bits):
                if bit == "1":
                    px, py = cursor + col, y + row
                    if 0 <= px < WIDTH and 0 <= py < HEIGHT:
                        matrix.set_pixel(px, py, *color)
        cursor += len(glyph[0]) + 1
    return cursor


def draw_text_centered(matrix, text: str, y: int, color: tuple[int, int, int]) -> None:
    draw_text(matrix, text, max(0, (WIDTH - text_width(text)) // 2), y, color)


def draw_text_right(matrix, text: str, right_x: int, y: int, color: tuple[int, int, int]) -> None:
    draw_text(matrix, text, right_x - text_width(text) + 1, y, color)


def draw_digit(matrix, char: str, x: int, y: int, color: tuple[int, int, int]) -> None:
    """Draw one seven-segment digit with its top-left corner at (x, y)."""
    on = _SEGMENTS.get(char, "")
    mid = y + (DIGIT_H - DIGIT_T) // 2
    inner_w = DIGIT_W - 2 * DIGIT_T
    right_x = x + DIGIT_W - DIGIT_T
    bottom_y = y + DIGIT_H - DIGIT_T
    upper_h = mid - (y + DIGIT_T)
    lower_h = bottom_y - (mid + DIGIT_T)

    if "a" in on:
        _fill(matrix, x + DIGIT_T, y, inner_w, DIGIT_T, color)
    if "g" in on:
        _fill(matrix, x + DIGIT_T, mid, inner_w, DIGIT_T, color)
    if "d" in on:
        _fill(matrix, x + DIGIT_T, bottom_y, inner_w, DIGIT_T, color)
    if "f" in on:
        _fill(matrix, x, y + DIGIT_T, DIGIT_T, upper_h, color)
    if "b" in on:
        _fill(matrix, right_x, y + DIGIT_T, DIGIT_T, upper_h, color)
    if "e" in on:
        _fill(matrix, x, mid + DIGIT_T, DIGIT_T, lower_h, color)
    if "c" in on:
        _fill(matrix, right_x, mid + DIGIT_T, DIGIT_T, lower_h, color)


def number_width(value: int) -> int:
    digits = len(str(value))
    return digits * DIGIT_W + (digits - 1) * DIGIT_GAP


def draw_number(matrix, value: int, x: int, y: int, color: tuple[int, int, int]) -> None:
    for i, char in enumerate(str(value)):
        draw_digit(matrix, char, x + i * (DIGIT_W + DIGIT_GAP), y, color)


def render(matrix, score: Score) -> None:
    """Draw the whole scoreboard. Does not clear or swap — the caller owns that."""
    draw_text(matrix, f"SATZ {score.set_number}", 1, Y_HEADER, COLOR_LABEL)
    draw_text_right(matrix, f"{score.sets[0]}:{score.sets[1]}", 62, Y_HEADER, COLOR_LABEL)
    _fill(matrix, 1, Y_DIVIDER, 62, 1, COLOR_FAINT)

    # The points. A finished match greens the winner and dims the loser, so the
    # final frame reads as a result rather than as a match still in progress.
    left_color, right_color = COLOR_SCORE, COLOR_SCORE
    if score.winner is not None:
        left_color = COLOR_WON if score.winner == 0 else COLOR_FAINT
        right_color = COLOR_WON if score.winner == 1 else COLOR_FAINT

    draw_number(matrix, score.points[0], LEFT_EDGE - number_width(score.points[0]) + 1, Y_SCORE, left_color)
    draw_number(matrix, score.points[1], RIGHT_START, Y_SCORE, right_color)
    _fill(matrix, COLON_X, Y_SCORE + 5, 3, 3, COLOR_LABEL)
    _fill(matrix, COLON_X, Y_SCORE + 12, 3, 3, COLOR_LABEL)

    serving_known = score.serving is not None and score.winner is None
    draw_text(
        matrix,
        score.names[0],
        5,
        Y_NAMES,
        COLOR_SERVE if serving_known and score.serving == 0 else COLOR_LABEL,
    )
    draw_text_right(
        matrix,
        score.names[1],
        58,
        Y_NAMES,
        COLOR_SERVE if serving_known and score.serving == 1 else COLOR_LABEL,
    )
    if serving_known:
        _fill(matrix, 1 if score.serving == 0 else 60, Y_NAMES + 1, 3, 3, COLOR_SERVE)

    if score.banner:
        color = COLOR_WON if score.winner is not None else COLOR_SERVE
        draw_text_centered(matrix, score.banner, Y_BANNER, color)

    if score.played:
        recent = " ".join(f"{a}-{b}" for a, b in score.played[-MAX_PLAYED:])
        draw_text_centered(matrix, recent, Y_PLAYED, COLOR_LABEL)

    if score.source:
        draw_text_centered(matrix, score.source, Y_SOURCE, COLOR_FAINT)


def display_score(matrix, config, wait) -> None:
    """Display mode entry point. Mirrors the still modes' clear/draw/hold cycle."""
    score = parse_score(config.text)
    if score is None:
        # Deliberately not a blank panel: a scoreboard that goes dark reads as
        # broken hardware, and the raw payload is at least a clue.
        logger.warning("score payload not parseable, falling back to static text: %r", config.text)
        from led_catcher.display.modes import _static_text

        _static_text(matrix, config, wait)
        return

    hold = getattr(config, "hold", False)
    matrix.clear()
    render(matrix, score)
    matrix.swap()

    wait(config.duration, hold)
    if hold:
        # Left lit on purpose — the next score draws over this one. Clearing
        # would blank the panel between two points.
        return
    matrix.clear()
    matrix.swap()
