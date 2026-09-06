"""The profile the kustomize OCI ships must cover every severity that matters.

Until 2026-09-05 the shipped default had rules for INFO/SUCCESS and WARNING
only. ERROR and CRITICAL matched nothing, so on the LED matrix they were
dropped (logged at debug, i.e. invisible) and in the web simulator they were
recorded in the same info blue as everything else. An alert that fails this way
looks exactly like an alert that worked.

The default lives in kcl/schema.k as the `profileData` attribute — the single
source for the ConfigMap in the published artifact — so this test reads it from
there rather than keeping a second copy that could drift.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from led_catcher.models import Message
from led_catcher.profile import Profile, load_profile, match_rule

SCHEMA_K = Path(__file__).parent.parent / "kcl" / "schema.k"

# Severities the homerun2 bus actually carries. omni-pitcher passes whatever a
# producer sends; these are the ones the ecosystem's own profiles and colour
# maps name. DEBUG is deliberately absent: it is not meant for a wall display,
# and since the no-match path now logs at info level that choice is visible
# rather than silent.
MUST_DISPLAY = ["error", "critical", "warning", "info", "success"]


def _shipped_profile(tmp_path: Path) -> Profile:
    """Extract the profileData default out of kcl/schema.k and load it."""
    source = SCHEMA_K.read_text()
    match = re.search(r'profileData:\s*str\s*=\s*"""\\?\n(.*?)"""', source, re.DOTALL)
    assert match, "could not find the profileData default in kcl/schema.k"
    body = match.group(1)

    # Fail loudly if the block stops being YAML rather than silently passing.
    parsed = yaml.safe_load(body)
    assert parsed and "displayRules" in parsed, "profileData is not a display profile"

    path = tmp_path / "profile.yaml"
    path.write_text(body)
    return load_profile(path)


@pytest.mark.parametrize("severity", MUST_DISPLAY)
def test_shipped_profile_matches_every_severity_that_matters(severity, tmp_path):
    profile = _shipped_profile(tmp_path)
    msg = Message(title="t", severity=severity, system="any-system")
    assert match_rule(profile, msg) is not None, (
        f"the shipped default profile has no rule for severity={severity!r} — "
        "such a message is dropped on the matrix and mis-coloured in the simulator"
    )


@pytest.mark.parametrize("severity", MUST_DISPLAY)
def test_shipped_profile_gives_every_severity_its_own_colour(severity, tmp_path):
    """A matched rule must not fall through to the white 'unknown severity'."""
    profile = _shipped_profile(tmp_path)
    msg = Message(title="t", severity=severity, system="any-system")
    config = match_rule(profile, msg)
    assert config is not None
    assert config.color != (255, 255, 255), (
        f"severity={severity!r} has no entry in the profile's colours, so it "
        "renders white — the colour that means 'I do not know this severity'"
    )


def test_critical_is_not_shown_as_info(tmp_path):
    """The specific regression: CRITICAL sharing INFO's blue."""
    profile = _shipped_profile(tmp_path)
    info = match_rule(profile, Message(title="t", severity="info", system="s"))
    critical = match_rule(profile, Message(title="t", severity="critical", system="s"))
    assert info is not None and critical is not None
    assert critical.color != info.color


# --- the tabletennis rule -------------------------------------------------
#
# A live score is a different kind of message from a notification: it replaces
# its predecessor rather than queueing behind it, and it should be readable
# between points instead of scrolling past once.


def test_a_tabletennis_score_gets_its_own_rule(tmp_path):
    profile = _shipped_profile(tmp_path)
    config = match_rule(profile, Message(title="7:5", severity="info", system="tabletennis"))

    assert config is not None
    assert config.kind == "static", "a score that scrolls away is not a scoreboard"
    assert config.hold is True, "the score should stay up until the next point replaces it"


def test_the_tabletennis_rule_renders_the_title_alone(tmp_path):
    """The panel is 64x64: about ten glyphs of 6x10, and nothing is ellipsised.

    `{{ system }}: {{ title }}` would render "tabletennis: 7:5" and run off the
    edge, which is why this rule exists at all rather than letting the wildcard
    take it.
    """
    profile = _shipped_profile(tmp_path)
    config = match_rule(profile, Message(title="SET 1:0", severity="success", system="tabletennis"))

    assert config is not None
    assert config.text == "SET 1:0"
    assert len(config.text) <= 10


def test_the_tabletennis_rule_wins_over_the_wildcard(tmp_path):
    """First match wins, so this rule has to sit above default-info.

    Moving it below is the easy mistake, and it fails silently: the score still
    displays, just as scrolling text with a prefix.
    """
    profile = _shipped_profile(tmp_path)

    for severity in ("info", "success"):
        config = match_rule(profile, Message(title="7:5", severity=severity, system="tabletennis"))
        assert config is not None
        assert config.kind == "static", f"the wildcard took severity={severity}"


def test_other_systems_are_unaffected_by_the_tabletennis_rule(tmp_path):
    """Adding a system-specific rule must not change what everything else does."""
    profile = _shipped_profile(tmp_path)

    for system in ("github", "k8s", "scale", "demo"):
        config = match_rule(profile, Message(title="t", severity="info", system=system))
        assert config is not None
        assert config.kind == "text", f"{system} stopped scrolling"
        assert config.text == f"{system}: t"


def test_a_tabletennis_error_still_falls_through_to_error_all(tmp_path):
    """The rule covers INFO and SUCCESS only.

    An ERROR from that system is not a score — it is something going wrong, and
    it should behave like every other error on the bus.
    """
    profile = _shipped_profile(tmp_path)
    config = match_rule(profile, Message(title="boom", severity="error", system="tabletennis"))

    assert config is not None
    assert config.kind == "text"
    assert config.hold is False
