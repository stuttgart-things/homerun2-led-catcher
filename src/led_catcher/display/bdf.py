"""BDF font metrics, read without rpi-rgb-led-matrix.

How wide a string renders is not optional information: a scroll has to know
when the text has finished leaving the panel, and centering has to know
whether it fits at all. `graphics.DrawText()` reports the width it drew, but
only on a Raspberry Pi and only after it has already drawn — no use for
choosing a starting x before the first frame, and no use to CI or the
simulator, where rgbmatrix is not installed.

So the numbers come from the BDF file itself, and mirror what
rpi-rgb-led-matrix reads out of the same file (`lib/bdf-font.cc`):

    FONTBOUNDINGBOX w h xoff yoff   →  height = h, baseline = h + yoff
    DWIDTH dx dy   (inside a glyph) →  the advance for that codepoint

A codepoint the font has no glyph for advances by nothing, which is what
`DrawText()` does with it.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from functools import lru_cache

logger = logging.getLogger(__name__)

# Distinct fonts a running process is expected to touch — see FONT_CACHE_SIZE
# in led_catcher.display.matrix, which caches the drawable fonts themselves.
METRICS_CACHE_SIZE = 32

FALLBACK_ADVANCE = 6
"""Per-character width used for a font whose metrics cannot be read.

Right for `6x10.bdf` and wrong for everything else. It was what the scroll
assumed for every font before #70; it survives only as the last resort for a
file we cannot parse, where no better number exists.
"""

FALLBACK_HEIGHT = 10
FALLBACK_BASELINE = 8

_FONTBOUNDINGBOX = re.compile(r"^FONTBOUNDINGBOX\s+(-?\d+)\s+(-?\d+)\s+(-?\d+)\s+(-?\d+)")
_ENCODING = re.compile(r"^ENCODING\s+(-?\d+)")
_DWIDTH = re.compile(r"^DWIDTH\s+(-?\d+)")


@dataclass(frozen=True)
class FontMetrics:
    """Everything a display mode needs to place text in a given font."""

    path: str
    height: int = FALLBACK_HEIGHT
    """Rows the font occupies — FONTBOUNDINGBOX height."""
    baseline: int = FALLBACK_BASELINE
    """Rows from the top of the font box down to the baseline `DrawText` takes as y."""
    advances: dict[int, int] = field(default_factory=dict)
    """Codepoint → pixels the pen advances, i.e. the glyph's DWIDTH."""

    @property
    def measured(self) -> bool:
        """False when the glyph table could not be read and widths are estimated."""
        return bool(self.advances)

    def text_width(self, text: str) -> int:
        """Pixels `DrawText` advances for `text` in this font."""
        if not self.measured:
            return len(text) * FALLBACK_ADVANCE
        return sum(self.advances.get(ord(char), 0) for char in text)

    def baseline_for_centered_text(self, panel_height: int) -> int:
        """The y to hand `DrawText` so the font box sits centered on the panel."""
        top = max(0, (panel_height - self.height) // 2)
        return top + self.baseline


def _parse(font_path: str) -> FontMetrics:
    height = FALLBACK_HEIGHT
    baseline = FALLBACK_BASELINE
    advances: dict[int, int] = {}
    codepoint: int | None = None

    with open(font_path, encoding="latin-1") as handle:
        for line in handle:
            if line.startswith("FONTBOUNDINGBOX"):
                match = _FONTBOUNDINGBOX.match(line)
                if match:
                    height = int(match.group(2))
                    # yoff is the box's offset below the baseline, and negative.
                    baseline = height + int(match.group(4))
            elif line.startswith("STARTCHAR"):
                codepoint = None
            elif line.startswith("ENCODING"):
                match = _ENCODING.match(line)
                if match:
                    value = int(match.group(1))
                    # An unencoded glyph carries ENCODING -1; nothing can ask for it.
                    codepoint = value if value >= 0 else None
            elif line.startswith("DWIDTH") and codepoint is not None:
                match = _DWIDTH.match(line)
                if match:
                    advances[codepoint] = int(match.group(1))

    return FontMetrics(path=font_path, height=height, baseline=baseline, advances=advances)


@lru_cache(maxsize=METRICS_CACHE_SIZE)
def load_metrics(font_path: str) -> FontMetrics:
    """Metrics for the BDF font at `font_path`, parsed once per path.

    Never raises: a font that cannot be read falls back to estimated widths
    rather than taking the display down with it.
    """
    try:
        metrics = _parse(font_path)
    except OSError as exc:
        logger.warning(
            "cannot read font metrics from %s (%s) — estimating %dpx per character",
            font_path,
            exc,
            FALLBACK_ADVANCE,
        )
        return FontMetrics(path=font_path)

    if not metrics.measured:
        logger.warning(
            "no glyph widths found in %s — estimating %dpx per character",
            font_path,
            FALLBACK_ADVANCE,
        )
    return metrics


def clear_metrics_cache() -> None:
    """Drop every cached set of metrics. For tests, and for a changed font directory."""
    load_metrics.cache_clear()
