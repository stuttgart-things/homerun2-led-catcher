"""A playlist: several images or GIFs, one after another (#146).

Each item gets ``duration`` seconds, in the order given or shuffled, until the
playlist is stopped or its ``total`` runs out. It plays whenever nothing else
is on the panel, like the idle screen and ahead of it: a message interrupts it,
and it picks up again once that message is done.

The schedule is a function of the wall clock, not of what the panel managed to
show: slot ``n`` starts at ``started + n * duration``, whatever interrupted the
panel meanwhile. That keeps a playlist with a ``total`` honest about when it
ends, and it lets anything that knows the playlist — the worker, ``GET
/display`` in web mode, the simulator canvas — agree on what is playing now
without the worker reporting every switch.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

ORDERS = ("sequence", "random")

MAX_PLAYLIST_ITEMS = 50


@dataclass(frozen=True)
class Slot:
    """One item's turn: what plays, from when until when (wall clock)."""

    position: int
    image: str
    start: float
    end: float


@dataclass(frozen=True)
class Playlist:
    images: tuple[str, ...]
    duration: float
    order: str = "sequence"
    total: float | None = None
    started: float = 0.0
    # Seeds the shuffle, so every reader of the playlist shuffles alike.
    seed: int = field(default_factory=lambda: random.SystemRandom().randrange(2**32))

    def __post_init__(self) -> None:
        if not self.images:
            raise ValueError("a playlist needs at least one image")
        if self.duration <= 0:
            raise ValueError("duration must be positive")
        if self.order not in ORDERS:
            raise ValueError(f"order must be one of {', '.join(ORDERS)}")

    @property
    def until(self) -> float | None:
        """When the playlist ends (wall clock), or None for endless."""
        return None if self.total is None else self.started + self.total

    def expired(self, now: float) -> bool:
        return self.until is not None and now >= self.until

    def slot(self, now: float) -> Slot | None:
        """What plays at ``now``. None once the playlist has run out."""
        if self.expired(now):
            return None
        n = max(0, int((now - self.started) // self.duration))
        start = self.started + n * self.duration
        end = start + self.duration
        if self.until is not None:
            end = min(end, self.until)
        return Slot(position=n, image=self.image_at(n), start=start, end=end)

    def image_at(self, n: int) -> str:
        """The image of slot ``n``."""
        count = len(self.images)
        if self.order == "sequence" or count < 3:
            # With two images a shuffle that never repeats one is alternation.
            return self.images[n % count]
        round_, index = divmod(n, count)
        return self._round(round_)[index]

    def _round(self, round_: int) -> list[str]:
        """One pass through every image, shuffled. A round does not start with
        the image the previous one ended on, so nothing plays twice in a row.
        Compared against the previous round as shuffled, before its own swap:
        a swap only touches its first two places, and with three or more images
        the last one is never among them."""
        order = self._shuffled(round_)
        if round_ > 0 and order[0] == self._shuffled(round_ - 1)[-1]:
            order[0], order[1] = order[1], order[0]
        return order

    def _shuffled(self, round_: int) -> list[str]:
        order = list(self.images)
        random.Random(f"{self.seed}:{round_}").shuffle(order)  # nosec B311 - display order, not security
        return order

    def describe(self, now: float) -> dict:
        """The playlist as ``GET /display`` reports it."""
        slot = self.slot(now)
        return {
            "images": list(self.images),
            "order": self.order,
            "duration": self.duration,
            "total": self.total,
            "started": self.started,
            "until": self.until,
            # The clock `start` and `end` are on, so a browser with another
            # one can tell how much of the slot is left.
            "now": now,
            "current": None
            if slot is None
            else {"position": slot.position, "image": slot.image, "start": slot.start, "end": slot.end},
        }
