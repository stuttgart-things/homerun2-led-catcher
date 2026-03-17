"""Tests for profile/rules engine."""

from pathlib import Path

from led_catcher.models import Message
from led_catcher.profile import Profile, load_profile, match_rule

PROFILE_PATH = Path(__file__).parent / "profile.yaml"


def test_load_profile():
    profile = load_profile(PROFILE_PATH)
    assert len(profile.rules) == 4
    assert "github-error" in profile.rules
    assert "scale-weight" in profile.rules
    assert "default-info" in profile.rules
    assert profile.colors["error"] == (255, 0, 0)


def test_load_profile_missing_file():
    profile = load_profile("/nonexistent/profile.yaml")
    assert len(profile.rules) == 0
    # Default colors should still be present
    assert "error" in profile.colors


def test_match_github_error():
    profile = load_profile(PROFILE_PATH)
    msg = Message(title="Build failed", severity="error", system="github")
    config = match_rule(profile, msg)
    assert config is not None
    assert config.kind == "gif"
    assert config.image == "sunset.gif"
    assert config.color == (255, 0, 0)


def test_match_gitlab_error():
    profile = load_profile(PROFILE_PATH)
    msg = Message(title="Pipeline failed", severity="ERROR", system="gitlab")
    config = match_rule(profile, msg)
    assert config is not None
    assert config.kind == "gif"


def test_match_scale_info():
    profile = load_profile(PROFILE_PATH)
    msg = Message(title="Weight", message="WEIGHT: 42", severity="info", system="scale")
    config = match_rule(profile, msg)
    assert config is not None
    assert config.kind == "static"
    assert config.text == "42g"


def test_match_wildcard_warning():
    profile = load_profile(PROFILE_PATH)
    msg = Message(title="Disk full", severity="warning", system="ansible")
    config = match_rule(profile, msg)
    assert config is not None
    assert config.kind == "text"
    assert config.text == "ansible: Disk full"
    assert config.color == (255, 165, 0)


def test_match_wildcard_info():
    profile = load_profile(PROFILE_PATH)
    msg = Message(title="Deploy OK", severity="info", system="flux")
    config = match_rule(profile, msg)
    assert config is not None
    assert config.text == "flux: Deploy OK"


def test_match_no_match():
    profile = load_profile(PROFILE_PATH)
    msg = Message(title="Debug trace", severity="debug", system="internal")
    config = match_rule(profile, msg)
    assert config is None


def test_jinja2_template_rendering():
    profile = load_profile(PROFILE_PATH)
    msg = Message(title="Alert", message="WEIGHT: 123", severity="info", system="scale")
    config = match_rule(profile, msg)
    assert config is not None
    assert config.text == "123g"


def test_empty_profile_no_match():
    profile = Profile()
    msg = Message(title="test", severity="info", system="github")
    config = match_rule(profile, msg)
    assert config is None
