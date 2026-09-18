#!/usr/bin/env python3
"""Draw the demo assets in `visual_aid/`.

The `image` and `gif` display modes had nothing to show on a fresh checkout:
`visual_aid/` held only a README, while the shipped profile referenced
`sunset.gif`, so `led-catcher-publish --demo` silently fell back to static text
with a `gif not found` line and the GIF path could not be checked on hardware
at all (#72, blocking #45).

The assets are drawn here rather than copied from the old `homerun-matrix-catcher`
Pi or fetched from the web: the legacy GIFs (nyan, explosion, …) are of unclear
provenance, and anything downloaded needs its licence establishing before it
can ship. What this script draws is the project's own work and ships under the
repository's licence, with no network involved.

    python hack/generate_demo_assets.py [--out visual_aid]

Deterministic: the same inputs give byte-identical files, so regenerating does
not churn the repository.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw

SIZE = 64
"""Panel size the assets are drawn for. They are scaled at display time anyway,
but drawing at the target size keeps them sharp and the files small."""

SUNSET_FRAMES = 24
SUNSET_FRAME_MS = 80


def _lerp(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    """Blend `a` into `b`. `t` is clamped, so no caller can mix past either end."""
    t = min(max(t, 0.0), 1.0)
    return tuple(round(start + (end - start) * t) for start, end in zip(a, b))


def draw_sunset(size: int = SIZE, frames: int = SUNSET_FRAMES) -> list[Image.Image]:
    """A sun sinking into a sea, as a loop.

    Chosen for the demo because it exercises what a panel is bad at: a smooth
    vertical gradient across 64 rows and a moving round edge. A GIF that looks
    right here looks right for anything.
    """
    sky_top = (26, 20, 72)
    sky_bottom = (255, 138, 61)
    sea = (18, 26, 74)
    sun = (255, 214, 92)
    horizon = size * 5 // 8

    out: list[Image.Image] = []
    for index in range(frames):
        # 0 at the top of the arc, 1 at the bottom, and back — so the loop has
        # no seam.
        phase = index / frames
        descent = 1 - abs(1 - 2 * phase)

        frame = Image.new("RGB", (size, size))
        draw = ImageDraw.Draw(frame)

        for y in range(horizon):
            draw.line([(0, y), (size, y)], fill=_lerp(sky_top, sky_bottom, y / max(horizon - 1, 1)))

        radius = size // 6
        # From clear of the horizon down to sitting on it, where the sea drawn
        # afterwards takes its lower half — so the sun actually sets.
        highest = horizon - radius - size // 5
        centre_y = round(highest + (horizon - highest) * descent)
        draw.ellipse(
            [size // 2 - radius, centre_y - radius, size // 2 + radius, centre_y + radius],
            fill=sun,
        )

        draw.rectangle([0, horizon, size, size], fill=sea)
        # The sun's reflection: a few broken lines under the horizon, drifting.
        for row in range(horizon + 2, size, 3):
            spread = (row - horizon) // 2 + 2
            offset = ((row + index) % 5) - 2
            centre = size // 2 + offset
            # Brightest just under the sun and fading downwards.
            fade = (row - horizon) / max(size - horizon, 1)
            draw.line(
                [(centre - spread, row), (centre + spread, row)],
                fill=_lerp(sea, sun, 0.6 * (1 - fade)),
            )

        out.append(frame)
    return out


def draw_test_pattern(size: int = SIZE) -> Image.Image:
    """An alignment and colour pattern for bringing a panel up.

    Every check in #45's display section is visible in one frame: the corner
    marks show whether anything is clipped at the edges, the border shows
    whether the panel is the size the software thinks it is, the ramps show
    colour channels and PWM depth, and the centre cross shows the midpoint.
    """
    image = Image.new("RGB", (size, size), (0, 0, 0))
    draw = ImageDraw.Draw(image)

    # One-pixel border: if a row or column of this is missing, the panel
    # geometry and the configured geometry disagree.
    draw.rectangle([0, 0, size - 1, size - 1], outline=(90, 90, 90))

    # Corner marks, each a different channel, so a rotated or mirrored panel is
    # obvious at a glance.
    for (x, y), color in (
        ((1, 1), (255, 0, 0)),
        ((size - 6, 1), (0, 255, 0)),
        ((1, size - 6), (0, 0, 255)),
        ((size - 6, size - 6), (255, 255, 255)),
    ):
        draw.rectangle([x, y, x + 4, y + 4], fill=color)

    # Channel ramps: solid at the left, black at the right. Banding here means
    # too few PWM bits.
    ramps = ((255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255))
    ramp_top = 12
    for row, color in enumerate(ramps):
        y = ramp_top + row * 6
        for x in range(4, size - 4):
            level = 1 - (x - 4) / (size - 9)
            draw.point((x, y), fill=tuple(round(channel * level) for channel in color))
            draw.point((x, y + 1), fill=tuple(round(channel * level) for channel in color))
            draw.point((x, y + 2), fill=tuple(round(channel * level) for channel in color))

    # Centre cross, on the exact midpoint.
    mid = size // 2
    draw.line([(mid, size - 20), (mid, size - 4)], fill=(200, 200, 200))
    draw.line([(mid - 8, size - 12), (mid + 8, size - 12)], fill=(200, 200, 200))

    # A 1px checkerboard beside it: a panel that cannot hold this shows grey.
    for y in range(size - 20, size - 4):
        for x in range(4, 20):
            if (x + y) % 2 == 0:
                draw.point((x, y), fill=(255, 255, 255))

    return image


def write(out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    frames = draw_sunset()
    sunset = out_dir / "sunset.gif"
    frames[0].save(
        sunset,
        save_all=True,
        append_images=frames[1:],
        duration=SUNSET_FRAME_MS,
        loop=0,
        optimize=True,
    )
    written.append(sunset)

    pattern = out_dir / "test-pattern.png"
    draw_test_pattern().save(pattern, optimize=True)
    written.append(pattern)

    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "visual_aid",
        help="directory to write into (default: %(default)s)",
    )
    args = parser.parse_args(argv)

    for path in write(args.out):
        print(f"wrote {path} ({path.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
