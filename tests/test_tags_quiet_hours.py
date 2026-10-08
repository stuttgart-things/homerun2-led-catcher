"""Tag filter in displayRules (#125) and quiet hours (#126)."""

from datetime import datetime, time
from zoneinfo import ZoneInfo

import pytest

from led_catcher.models import Message
from led_catcher.profile import load_profile, match_rule
from led_catcher.profile.engine import QuietHours

BERLIN = ZoneInfo("Europe/Berlin")

PROFILE = """
quietHours:
  from: "19:00"
  to: 07:00
  weekends: true
  timezone: Europe/Berlin
  allow: [error, critical]
displayRules:
  pr-report:
    systems: [homerun2-git-pitcher]
    tags: [issue_comment]
    kind: card
    duration: 15
  git:
    systems: [homerun2-git-pitcher]
    kind: card
    duration: 6
  everything:
    systems: ["*"]
    kind: text
"""

# Thursday, 2026-10-08
OFFICE = datetime(2026, 10, 8, 10, 0, tzinfo=BERLIN)
EVENING = datetime(2026, 10, 8, 22, 30, tzinfo=BERLIN)
EARLY = datetime(2026, 10, 9, 6, 59, tzinfo=BERLIN)
SATURDAY = datetime(2026, 10, 10, 12, 0, tzinfo=BERLIN)


@pytest.fixture
def profile(tmp_path):
    path = tmp_path / "profile.yaml"
    path.write_text(PROFILE)
    return load_profile(path)


def _msg(severity="info", tags="", system="homerun2-git-pitcher") -> Message:
    return Message(title="t", message="m", severity=severity, system=system, tags=tags)


def test_a_tagged_rule_matches_only_messages_with_that_tag(profile):
    report = match_rule(profile, _msg(tags="github,issue_comment,org/repo,morning"), now=OFFICE)
    pr = match_rule(profile, _msg(tags="github,pull_request,org/repo"), now=OFFICE)

    assert report.duration == 15
    assert pr.duration == 6


def test_a_tag_must_equal_a_whole_element(profile):
    # "issue_comment" is not a substring match against "issue_comment_edited"
    assert match_rule(profile, _msg(tags="github,issue_comment_edited"), now=OFFICE).duration == 6


def test_every_rule_tag_must_be_present(tmp_path):
    path = tmp_path / "p.yaml"
    path.write_text('displayRules:\n  both:\n    systems: ["*"]\n    tags: [a, b]\n')
    profile = load_profile(path)

    assert match_rule(profile, _msg(tags="a, b,c")) is not None
    assert match_rule(profile, _msg(tags="a")) is None
    assert match_rule(profile, _msg(tags="")) is None


@pytest.mark.parametrize("now", [EVENING, EARLY, SATURDAY])
def test_quiet_hours_hold_back_low_severities(profile, now):
    assert match_rule(profile, _msg("info"), now=now) is None
    assert match_rule(profile, _msg("warning"), now=now) is None


@pytest.mark.parametrize("now", [EVENING, SATURDAY])
def test_quiet_hours_let_allowed_severities_through(profile, now):
    assert match_rule(profile, _msg("error"), now=now) is not None
    assert match_rule(profile, _msg("CRITICAL"), now=now) is not None


def test_office_hours_show_everything(profile):
    assert match_rule(profile, _msg("info"), now=OFFICE) is not None


def test_unquoted_yaml_times_are_read_as_clock_times(profile):
    assert profile.quiet_hours.start == time(19, 0)
    assert profile.quiet_hours.end == time(7, 0)


def test_quiet_hours_within_a_day():
    quiet = QuietHours(start=time(12, 0), end=time(13, 0), timezone="Europe/Berlin")

    assert quiet.active(datetime(2026, 10, 8, 12, 30, tzinfo=BERLIN))
    assert not quiet.active(datetime(2026, 10, 8, 13, 0, tzinfo=BERLIN))


def test_quiet_hours_use_their_timezone():
    quiet = QuietHours(start=time(19, 0), end=time(7, 0), timezone="Europe/Berlin")

    # 17:30 UTC is 19:30 in Berlin (CEST)
    assert quiet.active(datetime(2026, 10, 8, 17, 30, tzinfo=ZoneInfo("UTC")))


def test_no_quiet_hours_without_config(tmp_path):
    path = tmp_path / "p.yaml"
    path.write_text('displayRules:\n  all:\n    systems: ["*"]\n')

    assert load_profile(path).quiet_hours is None
    assert match_rule(load_profile(path), _msg(), now=EVENING) is not None
