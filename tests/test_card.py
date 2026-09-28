"""Tests for the card display mode (#120) and the timestamp template variable.

No hardware and no Redis. The layout is checked as data, and the rendering as
pixels in an offscreen matrix that sets BDF glyphs the way the panel does.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
import yaml
from httpx import ASGITransport, AsyncClient

from led_catcher.display import card as card_mod
from led_catcher.display.bdf import load_metrics
from led_catcher.display.card import (
    COLOR_TITLE,
    DIVIDER_Y,
    ELLIPSIS,
    FOOTER_TOP,
    HEADER_HEIGHT,
    TEXT_X,
    TICKER_TOP,
    TITLE_BOTTOM,
    TITLE_TOP,
    CardContent,
    card_layout,
    display_card,
    draw_card,
    next_ticker_x,
    strip_title_prefix,
    truncate,
    wrap,
)
from led_catcher.display.modes import _resolve_font, display_event
from led_catcher.display.offscreen import ImageMatrix
from led_catcher.display.worker import DisplayWorker
from led_catcher.models import CaughtMessage, Message
from led_catcher.profile import DisplayConfig, Profile, load_profile, match_rule
from led_catcher.profile.engine import format_time, parse_timestamp
from led_catcher.web import EventTracker, create_web_app, create_web_handler
from tests.test_display_worker import FakeDisplay, wait_for

RED = (255, 0, 0)

# What homerun2-demo-pitcher sends.
DEMO = dict(
    system="prometheus",
    severity="error",
    title="[ERROR] prometheus pod crash loop detected",
    message="pod prometheus-0 restarted 5 times in the last 10 minutes",
    author="ops-bot",
    timestamp="2026-09-28T12:34:56Z",
    tags="k8s,monitoring",
)


@pytest.fixture
def berlin(monkeypatch):
    """Run in Europe/Berlin: CEST, UTC+2, on the demo timestamp's date."""
    monkeypatch.setenv("TZ", "Europe/Berlin")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def card_rule(**overrides) -> DisplayConfig:
    values = dict(kind="card", systems=["*"], severity=[], duration=10)
    values.update(overrides)
    return DisplayConfig(**values)


def resolve(rule: DisplayConfig, **message) -> DisplayConfig:
    config = match_rule(Profile(rules={"r": rule}), Message(**{**DEMO, **message}))
    assert config is not None
    return config


def width_in(font: str):
    return load_metrics(_resolve_font(font)).text_width


# ------------------------------------------------------------ prefix stripping


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("[ERROR] prometheus pod crash loop detected", "pod crash loop detected"),
        ("[error] Prometheus: pod crash loop detected", "pod crash loop detected"),
        ("[CRITICAL] prometheus - disk full", "disk full"),
        ("prometheus pod crash loop detected", "pod crash loop detected"),
        ("[ERROR] pod crash loop detected", "pod crash loop detected"),
        # Only the message's own system, and only as a whole word.
        ("prometheusx exporter down", "prometheusx exporter down"),
        ("[ERROR] grafana dashboard broken", "grafana dashboard broken"),
        # Brackets that are not a severity are part of the title.
        ("[prod] prometheus pod crash", "[prod] prometheus pod crash"),
        # Nothing left: keep the original rather than an empty title.
        ("[ERROR] prometheus", "[ERROR] prometheus"),
        ("", ""),
    ],
)
def test_strip_title_prefix(title, expected):
    assert strip_title_prefix(title, "error", "prometheus") == expected


# ------------------------------------------------------------------- wrapping


def by_chars(text: str) -> int:
    """A monospaced measure: 1px per character."""
    return len(text)


def test_wrap_breaks_on_words():
    lines, fitted = wrap("pod crash loop detected", 10, by_chars, 3)
    assert (lines, fitted) == (["pod crash", "loop", "detected"], True)


def test_wrap_hard_breaks_a_word_longer_than_a_line():
    lines, fitted = wrap("see kube-prometheus-stack now", 8, by_chars, 5)
    assert lines == ["see", "kube-pro", "metheus-", "stack", "now"]
    assert fitted


def test_wrap_truncates_with_an_ellipsis_when_out_of_lines():
    lines, fitted = wrap("one two three four five six", 9, by_chars, 2)
    # The last kept line fits as it is; it still ends in an ellipsis, because
    # the text goes on.
    assert (lines, fitted) == (["one two", "three" + ELLIPSIS], False)


def test_wrap_of_nothing_is_no_lines():
    assert wrap("   ", 10, by_chars, 3) == ([], True)


def test_truncate():
    assert truncate("ops-bot", 10, by_chars) == "ops-bot"
    assert truncate("flux-notifier", 8, by_chars) == "flux-no" + ELLIPSIS
    assert truncate("abc", 0, by_chars) == ""


# ----------------------------------------------------------------- timestamps


def test_parse_timestamp_converts_to_local_time(berlin):
    assert format_time("2026-09-28T12:34:56Z") == "14:34"
    assert format_time("2026-09-28T12:34:56+00:00") == "14:34"
    assert format_time("2026-09-28T14:34:56+02:00") == "14:34"


def test_parse_timestamp_takes_go_nanoseconds(berlin):
    # time.RFC3339Nano: more fractional digits than fromisoformat takes on 3.11.
    assert format_time("2026-09-28T12:34:56.123456789Z") == "14:34"


def test_a_timestamp_without_offset_is_local(berlin):
    assert format_time("2026-09-28T12:34:56") == "12:34"


@pytest.mark.parametrize("raw", ["", "  ", "yesterday", "12:34"])
def test_unparseable_timestamps(raw):
    assert parse_timestamp(raw) is None
    assert format_time(raw) == ""


def test_timestamp_is_a_template_variable_for_every_kind(berlin):
    config = resolve(card_rule(kind="static", text="{{ timestamp }}"))
    assert config.text == "2026-09-28T12:34:56Z"


def test_localtime_filter(berlin):
    config = resolve(card_rule(kind="text", text="{{ system }} {{ timestamp | localtime }}"))
    assert config.text == "prometheus 14:34"
    config = resolve(card_rule(kind="text", text="{{ timestamp | localtime('%d.%m. %H:%M') }}"))
    assert config.text == "28.09. 14:34"


def test_localtime_filter_passes_an_unparseable_value_through():
    config = resolve(card_rule(kind="text", text="{{ timestamp | localtime }}"), timestamp="soon")
    assert config.text == "soon"


def test_existing_templates_render_as_before():
    config = resolve(card_rule(kind="text", text="{{ system }}: {{ title | upper }}"))
    assert config.text == "prometheus: [ERROR] PROMETHEUS POD CRASH LOOP DETECTED"
    assert config.card is None


# ----------------------------------------------------------- profile and match


def test_profile_loads_a_card_rule(tmp_path):
    path = tmp_path / "profile.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "displayRules": {
                    "error-all": {
                        "systems": ["*"],
                        "severity": ["ERROR", "CRITICAL"],
                        "kind": "card",
                        "strip_prefix": True,
                        "duration": 10,
                    }
                }
            }
        )
    )
    rule = load_profile(path).rules["error-all"]
    assert (rule.kind, rule.strip_prefix, rule.duration) == ("card", True, 10.0)


def test_other_rules_do_not_strip_by_default(tmp_path):
    path = tmp_path / "profile.yaml"
    path.write_text("displayRules:\n  r:\n    systems: ['*']\n    kind: card\n")
    assert load_profile(path).rules["r"].strip_prefix is False


def test_a_matched_card_carries_the_whole_event(berlin):
    config = resolve(card_rule(strip_prefix=True))
    assert config.card == CardContent(
        system="prometheus",
        title="pod crash loop detected",
        message=DEMO["message"],
        author="ops-bot",
        time="14:34",
        tags="k8s,monitoring",
    )
    assert config.color == RED
    # The scrolling line is also the config's text, for logs and the timeline.
    assert config.text == DEMO["message"]


def test_the_title_is_kept_whole_without_strip_prefix():
    assert resolve(card_rule()).card.title == DEMO["title"]


def test_the_rule_text_replaces_the_scrolling_line():
    config = resolve(card_rule(text="{{ message }} ({{ tags }})"))
    assert config.card.message == DEMO["message"] + " (k8s,monitoring)"


def test_a_message_without_timestamp_shows_when_it_arrived():
    before = time.strftime("%H:%M")
    config = resolve(card_rule(), timestamp="")
    assert config.card.time in (before, time.strftime("%H:%M"))


def test_a_message_without_title_uses_its_body_as_the_title():
    assert resolve(card_rule(), title="").card.title == DEMO["message"]


# --------------------------------------------------------------------- layout


def content(**overrides) -> CardContent:
    values = dict(
        system="prometheus",
        title="pod crash loop detected",
        message=DEMO["message"],
        author="ops-bot",
        time="14:34",
        tags="k8s,monitoring",
    )
    values.update(overrides)
    return CardContent(**values)


def title_lines(layout):
    return [line for line in layout.lines if line.color == COLOR_TITLE]


def test_a_title_that_fits_gets_the_larger_font():
    lines = title_lines(card_layout(content(), RED))
    assert [Path(line.font).name for line in lines] == ["5x7.bdf"] * 3
    assert [line.text for line in lines] == ["pod crash", "loop", "detected"]


def test_a_longer_title_falls_back_to_4x6_on_four_lines():
    title = "application out of sync for more than fifteen minutes now"
    lines = title_lines(card_layout(content(title=title), RED))
    assert {Path(line.font).name for line in lines} == {"4x6.bdf"}
    assert len(lines) == 4
    assert " ".join(line.text for line in lines) == title


def test_a_title_too_long_for_4x6_is_truncated_with_an_ellipsis():
    title = "word " * 30
    lines = title_lines(card_layout(content(title=title), RED))
    assert len(lines) == 4
    assert lines[-1].text.endswith(ELLIPSIS)


def test_every_line_stays_inside_its_band_and_the_panel():
    layout = card_layout(content(title="application out of sync for more than fifteen minutes now"), RED)
    for line in layout.lines:
        metrics = load_metrics(line.font)
        top = line.baseline - metrics.baseline
        assert 0 <= top and top + metrics.height <= 64, line
        # The trailing blank column of the last glyph may fall off; ink may not.
        assert line.x >= 0 and line.x + metrics.text_width(line.text) <= 65, line
    for line in title_lines(layout):
        top = line.baseline - load_metrics(line.font).baseline
        assert TITLE_TOP <= top and top + load_metrics(line.font).height - 1 <= TITLE_BOTTOM


def test_the_system_name_is_upper_case_and_truncated_to_the_panel():
    header = card_layout(content(system="kube-prometheus-stack-operator"), RED).lines[0]
    assert header.text.startswith("KUBE-PROMETHEUS")
    assert header.text.endswith(ELLIPSIS)
    assert width_in("4x6.bdf")(header.text) <= 64


def test_the_footer_puts_the_time_right_and_truncates_the_author():
    layout = card_layout(content(author="flux-notification-controller"), RED)
    footer = [line for line in layout.lines if line.baseline == FOOTER_TOP + 5]
    author, clock = sorted(footer, key=lambda line: line.x)
    assert clock.text == "14:34"
    assert clock.x + width_in("4x6.bdf")("14:34") == 65
    assert author.text.endswith(ELLIPSIS)
    assert author.x + width_in("4x6.bdf")(author.text) < clock.x


def test_no_tags_no_tags_line():
    with_tags = card_layout(content(), RED)
    without = card_layout(content(tags=""), RED)
    assert len(with_tags.lines) == len(without.lines) + 1
    assert "k8s monitoring" in [line.text for line in with_tags.lines]


def test_a_short_message_does_not_scroll():
    assert card_layout(content(message="OOMKilled"), RED).scrolls is False
    assert card_layout(content(), RED).scrolls is True


def test_the_ticker_re_enters_from_the_right():
    assert next_ticker_x(10, 100) == 9
    assert next_ticker_x(-99, 100) == 64


def test_layout_json_names_fonts_not_paths():
    data = card_layout(content(), RED).to_json()
    assert data["ticker"]["font"] == "6x10.bdf"
    assert data["fills"][0] == [0, 0, 64, HEADER_HEIGHT, [255, 0, 0]]
    assert all("/" not in line["font"] for line in data["lines"])


# ------------------------------------------------------------------ rendering


def rendered(**overrides) -> ImageMatrix:
    matrix = ImageMatrix()
    draw_card(matrix, card_layout(content(**overrides), RED))
    return matrix


def lit_rows(matrix: ImageMatrix, color=None) -> set[int]:
    rows = set()
    for y in range(64):
        for x in range(64):
            pixel = matrix.image.getpixel((x, y))
            if pixel != (0, 0, 0) and (color is None or pixel == color):
                rows.add(y)
    return rows


def test_the_header_bar_is_the_severity_colour_with_dark_text():
    matrix = rendered()
    header = [matrix.image.getpixel((x, y)) for y in range(HEADER_HEIGHT) for x in range(64)]
    assert set(header) == {RED, (0, 0, 0)}
    # Most of the bar is lit; the text is cut out of it.
    assert header.count(RED) > len(header) * 0.7
    assert (0, 0, 0) in header


def test_each_part_is_drawn_in_its_own_band():
    matrix = rendered()
    title = lit_rows(matrix, COLOR_TITLE)
    assert title and min(title) >= TITLE_TOP and max(title) <= TITLE_BOTTOM
    ticker = lit_rows(matrix, RED) - set(range(HEADER_HEIGHT))
    assert ticker and min(ticker) >= TICKER_TOP and max(ticker) <= TICKER_TOP + 9
    assert DIVIDER_Y in lit_rows(matrix)
    assert max(lit_rows(matrix)) <= 63 and max(lit_rows(matrix)) >= FOOTER_TOP


def test_the_ticker_moves():
    layout = card_layout(content(), RED)
    first, later = ImageMatrix(), ImageMatrix()
    draw_card(first, layout, TEXT_X)
    draw_card(later, layout, TEXT_X - 12)
    band = (0, TICKER_TOP, 64, TICKER_TOP + 10)
    assert first.image.crop(band).tobytes() != later.image.crop(band).tobytes()
    header = (0, 0, 64, TICKER_TOP)
    assert first.image.crop(header).tobytes() == later.image.crop(header).tobytes()


# ------------------------------------------------------------------- the mode


class Clock:
    """monotonic() for the card loop, advanced by the fake wait."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch):
    fake = Clock()
    monkeypatch.setattr(card_mod.time, "monotonic", fake)
    return fake


def test_a_finite_card_scrolls_for_its_duration_then_clears(clock):
    waits = []

    def wait(seconds, hold=False, frame=False):
        assert not hold
        waits.append(seconds)
        clock.now += seconds

    matrix = ImageMatrix()
    config = resolve(card_rule(duration=2))
    display_card(matrix, config, wait)

    assert sum(waits) == pytest.approx(2, abs=0.05)
    assert len(waits) > 50  # ~one 30 ms frame each
    assert lit_rows(matrix) == set(), "the panel was not cleared"


def test_a_held_card_keeps_scrolling_until_replaced(clock):
    frames = []

    def wait(seconds, hold=False, frame=False):
        assert hold and frame
        frames.append(seconds)
        clock.now += seconds
        return len(frames) == 40  # replaced after 40 frames

    matrix = ImageMatrix()
    display_card(matrix, resolve(card_rule(hold=True, duration=1)), wait)

    assert len(frames) == 40
    assert lit_rows(matrix), "a held card must stay lit for the next display"


def test_a_card_whose_message_fits_is_a_still(clock):
    calls = []
    matrix = ImageMatrix()
    display_card(matrix, resolve(card_rule(duration=7), message="OOMKilled"), lambda *a, **k: calls.append((a, k)))
    assert calls == [((7.0, False), {})]


def test_display_event_routes_the_card_kind():
    matrix = ImageMatrix()
    display_event(matrix, resolve(card_rule(hold=True)))
    assert HEADER_HEIGHT - 1 in lit_rows(matrix, RED)


def test_a_card_config_without_content_shows_its_text_as_title():
    matrix = ImageMatrix()
    display_event(matrix, DisplayConfig(kind="card", text="HELLO", color=RED, hold=True))
    assert lit_rows(matrix, COLOR_TITLE)


class PixelDisplay(FakeDisplay):
    def set_pixel(self, x, y, r, g, b) -> None:
        pass


def test_the_worker_replaces_a_held_scrolling_card_at_once():
    display = PixelDisplay()
    worker = DisplayWorker(display)
    worker.start()
    try:
        worker.submit(resolve(card_rule(hold=True, duration=3600)))
        assert wait_for(lambda: DEMO["message"] in display.snapshot())
        assert wait_for(lambda: display.snapshot().count(DEMO["message"]) > 3), "the ticker is not moving"
        worker.submit(DisplayConfig(kind="static", text="NEXT", duration=0.05))
        assert wait_for(lambda: "NEXT" in display.snapshot(), timeout=1.0), "a held card did not give way"
    finally:
        worker.stop(timeout=2)


def test_a_held_card_counts_as_held():
    from led_catcher.display.worker import Showing

    assert Showing(resolve(card_rule(hold=True)), 0).held


# ------------------------------------------------------------------ simulator


async def test_the_simulator_gets_the_card_layout():
    tracker = EventTracker()
    profile = Profile(rules={"r": card_rule(strip_prefix=True)})
    create_web_handler(profile, tracker)(CaughtMessage(message=Message(**DEMO)))

    app = create_web_app(tracker)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        event = (await client.get("/api/events")).json()[0]

    assert event["kind"] == "card"
    card = event["card"]
    assert card["lines"][0]["text"] == "PROMETHEUS"
    assert card["ticker"]["text"] == DEMO["message"]
    assert card["scrolls"] is True


def test_the_canvas_draws_cards():
    template = (Path(__file__).parent.parent / "src" / "led_catcher" / "web" / "templates" / "index.html").read_text()
    assert "case 'card': return showCard(ev, token);" in template
    assert "const HOLDING_KINDS = ['static', 'image', 'score', 'card'];" in template
