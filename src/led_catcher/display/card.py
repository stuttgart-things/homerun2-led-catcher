"""The ``card`` display mode: a whole event on one 64x64 panel (#120).

The text modes draw one line of BDF text, which is enough for a headline and
too little for an alert: which system, what happened, the detail, who sent it
and when do not fit on one line. A card shows all of it at once:

```
 y  0- 7   header bar in the severity colour, system name in 4x6, dark
 y 10-33   title, word-wrapped: 5x7 on 3 lines, or 4x6 on 4 when it does not fit
 y 36-45   message in 6x10, scrolling for the display's duration
 y 48-53   tags in 4x6, dimmed (when there are any)
 y 55      divider, dimmed
 y 58-63   footer in 4x6: author left, HH:MM right
```

Text is set at x=1. A line may be 64px wide from there: in every font used
here a glyph's last column is blank, so what falls off the right edge is only
the space after the last character.

The layout is computed once per display (:func:`card_layout`) and handed to
the simulator as data, so the canvas draws the same lines at the same places
instead of keeping its own copy of these numbers (templates/index.html,
``showCard``). Only the ticker's movement is mirrored in JavaScript.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Laid out for 64x64, like the scoreboard. Another panel size gets the same
# rows from the top and is warned about.
WIDTH = 64
HEIGHT = 64

TEXT_X = 1
# The widest a line may be when set at TEXT_X: the trailing blank column of
# the last glyph may fall off the right edge (see the module docstring).
LINE_WIDTH = WIDTH - TEXT_X + 1

HEADER_TOP = 0
HEADER_HEIGHT = 8
HEADER_TEXT_TOP = 1
HEADER_FONT = "4x6.bdf"

TITLE_TOP = 10
TITLE_BOTTOM = 33
# Tried in order: the first font the whole title fits in wins; the last one
# truncates with an ellipsis. (font, line pitch in rows)
TITLE_FONTS: tuple[tuple[str, int], ...] = (("5x7.bdf", 8), ("4x6.bdf", 6))

TICKER_TOP = 36
TICKER_FONT = "6x10.bdf"

TAGS_TOP = 48
DIVIDER_Y = 55
FOOTER_TOP = 58
SMALL_FONT = "4x6.bdf"
# Space kept between the author and the time in the footer.
FOOTER_GAP = 4

ELLIPSIS = "…"

COLOR_HEADER_TEXT = (0, 0, 0)
COLOR_TITLE = (230, 230, 230)
COLOR_DIM = (110, 110, 110)
COLOR_DIVIDER = (50, 50, 50)


@dataclass(frozen=True)
class CardContent:
    """What a card shows, already rendered from the message and the rule."""

    system: str = ""
    title: str = ""
    message: str = ""
    author: str = ""
    time: str = ""
    tags: str = ""


@dataclass(frozen=True)
class CardText:
    """One line of text: DrawText(font, x, baseline, color, text)."""

    font: str
    x: int
    baseline: int
    color: tuple[int, int, int]
    text: str
    width: int = 0


@dataclass(frozen=True)
class CardFill:
    x: int
    y: int
    w: int
    h: int
    color: tuple[int, int, int]


@dataclass(frozen=True)
class CardLayout:
    """Everything on a card but the ticker's position."""

    fills: tuple[CardFill, ...]
    lines: tuple[CardText, ...]
    ticker: CardText
    # False when the message fits the panel: it is then drawn still at TEXT_X.
    scrolls: bool

    def to_json(self) -> dict:
        """The layout for the simulator canvas, fonts as bare file names."""

        def text(t: CardText) -> dict:
            return {
                "font": Path(t.font).name,
                "x": t.x,
                "baseline": t.baseline,
                "color": list(t.color),
                "text": t.text,
                "width": t.width,
            }

        return {
            "fills": [[f.x, f.y, f.w, f.h, list(f.color)] for f in self.fills],
            "lines": [text(t) for t in self.lines],
            "ticker": text(self.ticker),
            "scrolls": self.scrolls,
        }


# ------------------------------------------------------------------ text fit

Measure = Callable[[str], int]

# Severity words a producer puts in brackets at the start of a title.
_SEVERITY_WORDS = ("critical", "error", "err", "warning", "warn", "info", "success", "debug", "fatal")
_SEPARATORS = " \t:-|/>"


def strip_title_prefix(title: str, severity: str = "", system: str = "") -> str:
    """``title`` without a leading ``[SEVERITY]`` and a leading system name.

    Producers such as homerun2-demo-pitcher write titles like
    ``[ERROR] prometheus pod crash loop detected``; on a card the header bar
    already says both, and 64px is too narrow to say them twice. Only the
    message's own system is stripped, and only as a whole word. A title that
    would be left empty is returned unchanged.
    """
    stripped = title.strip()
    if stripped.startswith("["):
        end = stripped.find("]")
        word = stripped[1:end].strip().lower() if end > 0 else ""
        if word and (word == severity.strip().lower() or word in _SEVERITY_WORDS):
            stripped = stripped[end + 1 :].lstrip(_SEPARATORS)
    name = system.strip()
    if name and stripped.lower().startswith(name.lower()):
        rest = stripped[len(name) :]
        if not rest or rest[0] in _SEPARATORS:
            stripped = rest.lstrip(_SEPARATORS)
    return stripped or title.strip()


def truncate(text: str, max_width: int, measure: Measure, ellipsis: str = ELLIPSIS) -> str:
    """``text`` cut so it fits ``max_width``, ending in an ellipsis if it was cut."""
    if measure(text) <= max_width:
        return text
    cut = text
    while cut and measure(cut.rstrip() + ellipsis) > max_width:
        cut = cut[:-1]
    return cut.rstrip() + ellipsis if cut else ""


def wrap(text: str, max_width: int, measure: Measure, max_lines: int) -> tuple[list[str], bool]:
    """Word-wrap ``text`` into at most ``max_lines`` lines of ``max_width``.

    A word wider than a line is broken where it has to be. Returns the lines
    and whether everything fitted; when it did not, the last line ends in an
    ellipsis.
    """
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}" if current else word
        if measure(candidate) <= max_width:
            current = candidate
            continue
        if current:
            lines.append(current)
            current = ""
        # Hard-break a word that does not fit a line on its own.
        while measure(word) > max_width:
            head = word
            while len(head) > 1 and measure(head) > max_width:
                head = head[:-1]
            lines.append(head)
            word = word[len(head) :]
        current = word
    if current:
        lines.append(current)

    if len(lines) <= max_lines:
        return lines, True
    kept = lines[:max_lines]
    # The text goes on, so the last kept line says so even when it fits.
    last = kept[-1]
    while last and measure(last + ELLIPSIS) > max_width:
        last = last[:-1]
    kept[-1] = last.rstrip() + ELLIPSIS
    return kept, False


# ----------------------------------------------------------------- the layout


def card_layout(content: CardContent, color: tuple[int, int, int], width: int = WIDTH) -> CardLayout:
    """Lay ``content`` out as a card in the severity ``color``."""
    from led_catcher.display.bdf import load_metrics
    from led_catcher.display.modes import _resolve_font

    small = _resolve_font(SMALL_FONT)
    small_m = load_metrics(small)
    header = _resolve_font(HEADER_FONT)
    header_m = load_metrics(header)
    line_width = width - TEXT_X + 1

    fills = [CardFill(0, HEADER_TOP, width, HEADER_HEIGHT, tuple(color))]
    lines: list[CardText] = []

    system = truncate(content.system.upper(), line_width, header_m.text_width)
    lines.append(CardText(header, TEXT_X, HEADER_TEXT_TOP + header_m.baseline, COLOR_HEADER_TEXT, system))

    # The title in the largest font it fits in.
    title_rows = TITLE_BOTTOM - TITLE_TOP + 1
    title_lines: list[str] = []
    for index, (font_name, pitch) in enumerate(TITLE_FONTS):
        font = _resolve_font(font_name)
        metrics = load_metrics(font)
        max_lines = max(1, (title_rows - metrics.height) // pitch + 1)
        title_lines, fitted = wrap(content.title, line_width, metrics.text_width, max_lines)
        if fitted or index == len(TITLE_FONTS) - 1:
            break
    for row, text in enumerate(title_lines):
        top = TITLE_TOP + row * pitch
        lines.append(CardText(font, TEXT_X, top + metrics.baseline, COLOR_TITLE, text))

    if content.tags:
        tags = truncate(
            " ".join(t.strip() for t in content.tags.split(",") if t.strip()), line_width, small_m.text_width
        )
        lines.append(CardText(small, TEXT_X, TAGS_TOP + small_m.baseline, COLOR_DIM, tags))

    fills.append(CardFill(0, DIVIDER_Y, width, 1, COLOR_DIVIDER))

    footer_baseline = FOOTER_TOP + small_m.baseline
    author_room = line_width
    if content.time:
        time_width = small_m.text_width(content.time)
        # Right-aligned with the trailing blank column off the edge.
        time_x = width - time_width + 1
        lines.append(CardText(small, time_x, footer_baseline, COLOR_DIM, content.time))
        author_room = time_x - FOOTER_GAP - TEXT_X
    if content.author:
        author = truncate(content.author, author_room, small_m.text_width)
        lines.append(CardText(small, TEXT_X, footer_baseline, COLOR_DIM, author))

    ticker_font = _resolve_font(TICKER_FONT)
    ticker_m = load_metrics(ticker_font)
    ticker_width = ticker_m.text_width(content.message)
    ticker = CardText(
        ticker_font,
        TEXT_X,
        TICKER_TOP + ticker_m.baseline,
        tuple(color),
        content.message,
        ticker_width,
    )
    return CardLayout(fills=tuple(fills), lines=tuple(lines), ticker=ticker, scrolls=ticker_width > line_width)


def next_ticker_x(x: int, text_width: int, width: int = WIDTH) -> int:
    """The ticker's next position: 1px left, re-entering from the right once gone."""
    x -= 1
    if x <= -text_width:
        return width
    return x


def draw_card(matrix, layout: CardLayout, ticker_x: int = TEXT_X) -> None:
    """Draw one frame of the card. Does not clear or swap — the caller owns that."""
    for fill in layout.fills:
        for y in range(fill.y, min(fill.y + fill.h, matrix.height)):
            for x in range(fill.x, min(fill.x + fill.w, matrix.width)):
                matrix.set_pixel(x, y, *fill.color)
    for line in layout.lines:
        if line.text:
            matrix.draw_text(line.font, line.x, line.baseline, line.color, line.text)
    ticker = layout.ticker
    if ticker.text:
        matrix.draw_text(ticker.font, ticker_x, ticker.baseline, ticker.color, ticker.text)


# ------------------------------------------------------------------- the mode


def content_of(config) -> CardContent:
    """The card a DisplayConfig carries, or one built from its text.

    A profile rule gets its content from the message (profile.engine). Anything
    else that asks for a card — a hand-built config, a test — at least shows
    its text as the title rather than an empty card.
    """
    card = getattr(config, "card", None)
    if card is not None:
        return card
    return CardContent(title=getattr(config, "text", "") or "---")


def display_card(matrix, config, wait) -> None:
    """Display mode entry point.

    Finite: the card stays up for ``duration`` seconds, the ticker scrolling
    the whole time. Held: it stays up until replaced, still scrolling, and is
    left lit for the next display to draw over.
    """
    from led_catcher.display.modes import SCROLL_FRAME_SECONDS

    _warn_once_about_panel_size(matrix)
    layout = card_layout(content_of(config), config.color, matrix.width)
    hold = getattr(config, "hold", False)
    duration = config.duration

    if not layout.scrolls:
        matrix.clear()
        draw_card(matrix, layout)
        matrix.swap()
        wait(duration, hold)
        if not hold:
            matrix.clear()
            matrix.swap()
        return

    x = TEXT_X
    start = time.monotonic()
    # As in the GIF loop: driven by the time scheduled as well as the clock,
    # so a wait that returns early (shutdown) cannot spin the loop flat out.
    scheduled = 0.0
    while True:
        drawn_at = time.monotonic()
        matrix.clear()
        draw_card(matrix, layout, x)
        matrix.swap()
        x = next_ticker_x(x, layout.ticker.width, matrix.width)
        pause = max(0.0, SCROLL_FRAME_SECONDS - (time.monotonic() - drawn_at))
        if hold:
            # One frame of a held display: ends when something replaces it.
            if wait(pause, True, frame=True):
                return
            continue
        scheduled += SCROLL_FRAME_SECONDS
        elapsed = time.monotonic() - start
        if scheduled >= duration or elapsed >= duration:
            break
        wait(min(pause, duration - elapsed))

    matrix.clear()
    matrix.swap()


def _warn_once_about_panel_size(matrix) -> None:
    width = getattr(matrix, "width", WIDTH)
    height = getattr(matrix, "height", HEIGHT)
    if (width, height) == (WIDTH, HEIGHT) or _warn_once_about_panel_size.warned:
        return
    _warn_once_about_panel_size.warned = True
    logger.warning(
        "the card display is laid out for %dx%d and this panel is %dx%d — rows are placed from the top",
        WIDTH,
        HEIGHT,
        width,
        height,
    )


_warn_once_about_panel_size.warned = False
