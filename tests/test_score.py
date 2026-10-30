"""Tests for the table tennis scoreboard display mode.

No hardware and no Redis: the fake matrix below records what would have been
lit, which is the only thing the renderer actually decides.
"""

from __future__ import annotations

import pytest

from led_catcher.display import score as score_mod
from led_catcher.display.modes import display_event
from led_catcher.display.score import (
    DIGIT_H,
    DIGIT_W,
    Score,
    display_score,
    format_score,
    number_width,
    parse_score,
    render,
    text_width,
)
from led_catcher.profile.engine import DisplayConfig


class FakeMatrix:
    """Records pixels instead of lighting them."""

    width = 64
    height = 64

    def __init__(self) -> None:
        self.pixels: dict[tuple[int, int], tuple[int, int, int]] = {}
        self.clears = 0
        self.swaps = 0
        self.texts: list[tuple[int, int, str]] = []

    def set_pixel(self, x: int, y: int, r: int, g: int, b: int) -> None:
        self.pixels[(x, y)] = (r, g, b)

    def draw_text(self, font_path: str, x: int, y: int, color, text: str) -> int:
        self.texts.append((x, y, text))
        return len(text) * 6

    def clear(self) -> None:
        self.clears += 1
        self.pixels.clear()

    def swap(self) -> None:
        self.swaps += 1


def noop_wait(seconds: float, hold: bool = False) -> None:
    return None


# ------------------------------------------------------------------- parsing


def test_parse_full_payload():
    s = parse_score("points=8:6;sets=1:1;set=3;serve=b;a=Anja;b=Ben;played=11-8,9-11;source=button-a")
    assert s is not None
    assert s.points == (8, 6)
    assert s.sets == (1, 1)
    assert s.set_number == 3
    assert s.serving == 1
    assert s.names == ("ANJA", "BEN")
    assert s.played == [(11, 8), (9, 11)]
    assert s.source == "BUTTON-A"


def test_parse_defaults_when_only_points_given():
    s = parse_score("points=0:0")
    assert s is not None
    assert s.sets == (0, 0)
    assert s.set_number == 1
    assert s.serving is None
    assert s.names == ("A", "B")
    assert s.played == []


def test_parse_ignores_unknown_keys():
    """The pitcher must be able to add fields without a lockstep release."""
    s = parse_score("points=3:2;rally_length=17;future=whatever")
    assert s is not None
    assert s.points == (3, 2)


@pytest.mark.parametrize("raw", ["", "   ", "sets=1:1", "points=", "points=abc", "nonsense"])
def test_parse_rejects_payload_without_usable_points(raw):
    assert parse_score(raw) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("serve=a", 0), ("serve=b", 1), ("serve=0", 0), ("serve=1", 1), ("serve=x", None), ("serve=", None)],
)
def test_parse_serving_side(raw, expected):
    s = parse_score(f"points=1:1;{raw}")
    assert s is not None
    assert s.serving == expected


def test_parse_truncates_long_names_rather_than_overflowing():
    s = parse_score("points=1:1;a=Bartholomew;b=X")
    assert s is not None
    assert len(s.names[0]) <= 6
    assert s.names[0] == "BARTHO"


def test_parse_skips_malformed_played_sets_but_keeps_the_rest():
    s = parse_score("points=1:1;played=11-8,junk,9-11")
    assert s is not None
    assert s.played == [(11, 8), (9, 11)]


def test_parse_zaehlwerks_tags_as_a_board():
    """zaehlwerk sends the board as message tags: commas, player_a/player_b,
    finished sets separated by spaces. The routing tags around it are ignored."""
    s = parse_score(
        "match=m1,set=3,transition=point,side=b,"
        "points=5:10,sets=1:1,serve=b,player_a=Anna,player_b=Bernd,played=11-8 9-11"
    )
    assert s is not None
    assert s.points == (5, 10)
    assert s.sets == (1, 1)
    assert s.set_number == 3
    assert s.serving == 1
    assert s.names == ("ANNA", "BERND")
    assert s.played == [(11, 8), (9, 11)]


def test_parse_a_bare_score_as_a_vs_b():
    s = parse_score("7:5")
    assert s is not None
    assert s.points == (7, 5)
    assert s.names == ("A", "B")
    assert s.serving is None


@pytest.mark.parametrize("raw", ["SET 1:0", "WIN 3:1"])
def test_a_set_or_match_title_is_no_board(raw):
    assert parse_score(raw) is None


@pytest.mark.parametrize(
    "raw",
    [
        "points=8:6;sets=1:1;set=3;serve=b;a=ANJA;b=BEN;played=11-8,9-11;banner=SATZBALL;source=BUTTON-A",
        "points=11:9;sets=3:1;set=4;winner=a;a=A;b=B",
        "points=0:0;sets=0:0;set=1;a=A;b=B",
        "points=3:2;sets=0:0;set=1;serve=a;a=CLEO;b=EMIL;color_a=#3aa6ff;color_b=#4d7c4a",
    ],
)
def test_format_score_round_trips_the_payload(raw):
    s = parse_score(raw)
    assert s is not None
    assert format_score(s) == raw
    assert parse_score(format_score(s)) == s


# ------------------------------------------------------------------ geometry


def test_number_width_matches_digit_geometry():
    assert number_width(7) == DIGIT_W
    assert number_width(11) == 2 * DIGIT_W + 2


def test_text_width_of_empty_string_is_zero():
    assert text_width("") == 0


def test_every_digit_lights_pixels_inside_its_own_box():
    """A digit must stay in its cell — bleeding into the next one is invisible
    in code and obvious on the panel."""
    for digit in "0123456789":
        m = FakeMatrix()
        score_mod.draw_digit(m, digit, 10, 20, (255, 255, 255))
        assert m.pixels, f"digit {digit} lit nothing"
        xs = [x for x, _ in m.pixels]
        ys = [y for _, y in m.pixels]
        assert min(xs) >= 10 and max(xs) < 10 + DIGIT_W
        assert min(ys) >= 20 and max(ys) < 20 + DIGIT_H


def test_digits_are_distinguishable_from_one_another():
    shapes = set()
    for digit in "0123456789":
        m = FakeMatrix()
        score_mod.draw_digit(m, digit, 0, 0, (255, 255, 255))
        shapes.add(frozenset(m.pixels))
    assert len(shapes) == 10


def test_one_is_narrower_on_screen_but_keeps_its_cell():
    m_one, m_eight = FakeMatrix(), FakeMatrix()
    score_mod.draw_digit(m_one, "1", 0, 0, (255, 255, 255))
    score_mod.draw_digit(m_eight, "8", 0, 0, (255, 255, 255))
    assert len(m_one.pixels) < len(m_eight.pixels)


# ------------------------------------------------------------------ rendering


def test_render_stays_inside_the_panel():
    m = FakeMatrix()
    render(
        m,
        Score(
            points=(11, 9),
            sets=(2, 2),
            set_number=5,
            serving=1,
            names=("ANJA", "BEN"),
            played=[(11, 8), (9, 11), (11, 6), (7, 11)],
            banner="MATCHBALL ANJA",
            source="BUTTON-A",
        ),
    )
    assert m.pixels
    for x, y in m.pixels:
        assert 0 <= x < 64
        assert 0 <= y < 64


def test_two_digit_score_does_not_collide_with_the_colon():
    m = FakeMatrix()
    render(m, Score(points=(11, 11)))
    # The colon occupies x 30..32; both numbers must stay clear of it.
    left = [x for x, y in m.pixels if score_mod.Y_SCORE <= y < score_mod.Y_SCORE + DIGIT_H and x < 30]
    right = [x for x, y in m.pixels if score_mod.Y_SCORE <= y < score_mod.Y_SCORE + DIGIT_H and x > 32]
    assert max(left) <= score_mod.LEFT_EDGE
    assert min(right) >= score_mod.RIGHT_START


def test_serving_dot_is_drawn_only_when_the_side_is_known():
    without = FakeMatrix()
    render(without, Score(points=(1, 1), serving=None))
    with_serve = FakeMatrix()
    render(with_serve, Score(points=(1, 1), serving=0))
    assert len(with_serve.pixels) > len(without.pixels)


def test_finished_match_does_not_show_a_serving_dot():
    m = FakeMatrix()
    render(m, Score(points=(11, 9), serving=0, winner=0))
    assert score_mod.COLOR_SERVE not in m.pixels.values()


def test_winner_is_greened_and_loser_dimmed():
    m = FakeMatrix()
    render(m, Score(points=(11, 9), winner=0, sets=(3, 1)))
    colors = set(m.pixels.values())
    assert score_mod.COLOR_WON in colors
    assert score_mod.COLOR_SCORE not in colors


def test_only_the_last_three_sets_are_shown():
    many = FakeMatrix()
    render(many, Score(points=(1, 1), played=[(11, 8), (9, 11), (11, 6), (7, 11)]))
    three = FakeMatrix()
    render(three, Score(points=(1, 1), played=[(9, 11), (11, 6), (7, 11)]))
    row = score_mod.Y_PLAYED
    assert {p for p in many.pixels if row <= p[1] < row + 5} == {p for p in three.pixels if row <= p[1] < row + 5}


# ----------------------------------------------------------------- the mode


def test_display_score_holds_the_panel_when_asked():
    m = FakeMatrix()
    display_score(m, DisplayConfig(kind="score", text="points=8:6", hold=True, duration=0), noop_wait)
    # Cleared once before drawing, and left lit afterwards.
    assert m.clears == 1
    assert m.pixels


def test_display_score_clears_after_a_finite_duration():
    m = FakeMatrix()
    display_score(m, DisplayConfig(kind="score", text="points=8:6", hold=False, duration=0), noop_wait)
    assert m.clears == 2
    assert not m.pixels


def test_unparseable_payload_falls_back_to_text_instead_of_blanking():
    m = FakeMatrix()
    display_score(m, DisplayConfig(kind="score", text="not a score", hold=True, duration=0), noop_wait)
    assert m.texts, "expected the raw payload to be drawn as static text"
    assert "not a score" in m.texts[0][2]


def test_display_event_routes_the_score_kind():
    m = FakeMatrix()
    display_event(m, DisplayConfig(kind="score", text="points=8:6", hold=True, duration=0), noop_wait)
    assert m.pixels
    assert not m.texts, "score payload should render as pixels, not as BDF text"


# ------------------------------------------------------------ player colours


def test_parse_the_players_colours_from_zaehlwerks_tags():
    s = parse_score("match=m1,set=1,points=3:2,serve=a,player_a=Cleo,player_b=Emil,color_a=#3aa6ff,color_b=#4D7C4A")
    assert s is not None
    assert s.colors == ((0x3A, 0xA6, 0xFF), (0x4D, 0x7C, 0x4A))


@pytest.mark.parametrize(
    "raw",
    ["color_a=#3aa6ff", "color_a=#3aa6ff;color_b=green", "color_a=#3aa6ff;color_b=#4d7c4", "color_b=#4d7c4a"],
)
def test_colours_count_only_both_together(raw):
    s = parse_score(f"points=1:1;{raw}")
    assert s is not None
    assert s.colors is None


def _name_pixels(m: FakeMatrix, left: bool) -> set[tuple[int, int, int]]:
    rows = range(score_mod.Y_NAMES, score_mod.Y_NAMES + 5)
    return {c for (x, y), c in m.pixels.items() if y in rows and (5 <= x < 30 if left else 34 <= x <= 58)}


def test_the_names_take_the_players_colours_and_the_marker_turns_white():
    m = FakeMatrix()
    blue, green = (0x3A, 0xA6, 0xFF), (0x4D, 0x7C, 0x4A)
    render(m, Score(points=(3, 2), serving=0, names=("CLEO", "EMIL"), colors=(blue, green)))
    assert _name_pixels(m, left=True) == {blue}
    assert _name_pixels(m, left=False) == {green}
    assert m.pixels[(2, score_mod.Y_NAMES + 2)] == score_mod.COLOR_MARKER
    assert score_mod.COLOR_SERVE not in m.pixels.values(), "orange is the server without colours only"


def test_without_colours_the_server_is_orange_as_before():
    m = FakeMatrix()
    render(m, Score(points=(3, 2), serving=0, names=("CLEO", "EMIL")))
    assert _name_pixels(m, left=True) == {score_mod.COLOR_SERVE}
    assert _name_pixels(m, left=False) == {score_mod.COLOR_LABEL}
    assert m.pixels[(2, score_mod.Y_NAMES + 2)] == score_mod.COLOR_SERVE
