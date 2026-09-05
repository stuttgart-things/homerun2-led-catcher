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
