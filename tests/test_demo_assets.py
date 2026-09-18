"""The shipped profile's images exist, and `--demo` reaches both modes (#72).

`visual_aid/` held only a README while `tests/profile.yaml` referenced
`sunset.gif`, so a fresh checkout logged `gif not found` and fell back to static
text — the `gif` and `image` paths could not be seen at all, on hardware or off
it (#45).

These tests are the reason the assets cannot quietly go missing again: a rule
naming an image that is not there fails here rather than on the panel.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from PIL import Image

from led_catcher.display.frames import load_animation
from led_catcher.display.modes import _resolve_image
from led_catcher.tools.publish import DEMO_STEPS

REPO_ROOT = Path(__file__).resolve().parent.parent
VISUAL_AID = REPO_ROOT / "visual_aid"
PROFILE = REPO_ROOT / "tests" / "profile.yaml"
PANEL = 64


def profile_rules() -> dict:
    return yaml.safe_load(PROFILE.read_text())["displayRules"]


def image_rules() -> dict:
    return {name: rule for name, rule in profile_rules().items() if rule.get("image")}


def test_the_profile_still_has_rules_that_need_an_image():
    """Otherwise the tests below would pass by having nothing to check."""
    kinds = {rule["kind"] for rule in image_rules().values()}

    assert "gif" in kinds
    assert "image" in kinds


@pytest.mark.parametrize("name", sorted({rule["image"] for rule in image_rules().values()}))
def test_every_image_the_profile_names_resolves(name):
    resolved = _resolve_image(name)

    assert resolved is not None, f"{name} is referenced by tests/profile.yaml but not in visual_aid/"
    assert resolved.is_file()


@pytest.mark.parametrize("name", ["sunset.gif", "test-pattern.png"])
def test_the_shipped_assets_are_panel_sized(name):
    with Image.open(VISUAL_AID / name) as image:
        assert image.size == (PANEL, PANEL), "drawn at panel size so nothing is scaled at display time"


def test_the_shipped_gif_actually_animates():
    animation = load_animation(VISUAL_AID / "sunset.gif", PANEL, PANEL)

    assert animation is not None
    assert animation.animated, "a still image would leave the gif mode untested on hardware"
    assert animation.loop_duration > 1.0, "long enough to see it loop"


def test_the_demo_walks_through_both_image_modes():
    expected = {step.expect for step in DEMO_STEPS}

    assert "gif" in expected
    assert "image" in expected


@pytest.mark.parametrize("step", DEMO_STEPS, ids=lambda step: f"{step.system}-{step.severity}")
def test_every_demo_step_matches_a_rule_in_the_shipped_profile(step):
    from led_catcher.models import Message
    from led_catcher.profile import load_profile, match_rule

    profile = load_profile(str(PROFILE))
    message = Message(title=step.title, message=step.message, severity=step.severity, system=step.system)
    config = match_rule(profile, message)

    assert config is not None, f"--demo sends {step.system}/{step.severity} and no rule matches it"
    assert config.kind in step.expect, f"--demo promises {step.expect!r} and the profile gives {config.kind!r}"
    if config.image:
        assert _resolve_image(config.image) is not None
