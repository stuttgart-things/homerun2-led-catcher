#!/usr/bin/env python3
"""Render a `card` display to a PNG, without a panel.

The event goes through the profile engine exactly as one from the stream
would (rule matching, prefix stripping, the timestamp in local time), and the
card is drawn by the panel's own `display_card` into an offscreen matrix. The
first frame is saved: the ticker at its start position.

    python hack/render_card.py --out card.png
    python hack/render_card.py --severity warning --title "disk 85% on node-01" --out warn.png

`--scale 1` writes the 64x64 frame as the panel shows it; the default enlarges
it to LED dots so it can be looked at.
"""

from __future__ import annotations

import argparse

from led_catcher.display.card import display_card
from led_catcher.display.offscreen import ImageMatrix
from led_catcher.models import Message
from led_catcher.profile.engine import DisplayConfig, Profile, match_rule


class _FirstFrame(Exception):
    pass


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--system", default="prometheus")
    parser.add_argument("--severity", default="error")
    parser.add_argument("--title", default="[ERROR] prometheus pod crash loop detected")
    parser.add_argument("--message", default="pod prometheus-0 restarted 5 times in the last 10 minutes")
    parser.add_argument("--author", default="ops-bot")
    parser.add_argument("--timestamp", default="2026-09-28T12:34:56Z")
    parser.add_argument("--tags", default="k8s,monitoring")
    parser.add_argument("--no-strip", action="store_true", help="keep the title's [SEVERITY] and system prefix")
    parser.add_argument("--scale", type=int, default=8, help="pixels per LED in the PNG (1: the raw 64x64 frame)")
    parser.add_argument("--out", default="card.png")
    args = parser.parse_args()

    rule = DisplayConfig(kind="card", strip_prefix=not args.no_strip, systems=["*"], duration=10)
    msg = Message(
        system=args.system,
        severity=args.severity,
        title=args.title,
        message=args.message,
        author=args.author,
        timestamp=args.timestamp,
        tags=args.tags,
    )
    config = match_rule(Profile(rules={"card": rule}), msg)

    matrix = ImageMatrix()

    def first_frame(seconds: float, hold: bool = False, frame: bool = False):
        raise _FirstFrame

    try:
        display_card(matrix, config, first_frame)
    except _FirstFrame:
        pass
    image = matrix.snapshot() if args.scale <= 1 else matrix.enlarged(args.scale)
    image.save(args.out)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
