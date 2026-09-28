"""Profile/rules engine for display mode routing.

Maps (system, severity) combinations to LED display configurations
using YAML profiles with first-match semantics.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import jinja2
import yaml

from led_catcher.display.card import CardContent, strip_title_prefix
from led_catcher.models import Message

logger = logging.getLogger(__name__)

# Default severity colors (RGB)
DEFAULT_COLORS: dict[str, tuple[int, int, int]] = {
    "error": (255, 0, 0),
    "warning": (255, 165, 0),
    "success": (0, 255, 0),
    "info": (0, 100, 255),
    "debug": (128, 128, 128),
}


@dataclass
class DisplayConfig:
    """Configuration for how to display a matched message."""

    kind: str = "text"  # static, text, ticker, image, gif, score, card
    text: str = ""
    image: str = ""
    font: str = "6x10.bdf"
    duration: float = 5.0
    # hold keeps the display up until another message replaces it, instead of
    # clearing after `duration`. For a live scoreboard that is the whole point:
    # the score should be readable between points, not for five seconds after
    # each one. A held display is by definition pre-emptible — that is what
    # "until replaced" means — while a finite duration gets its time.
    #
    # Only the still modes honour it (static, image, score) and the card. The
    # animated ones run for as long as their animation takes; see
    # docs/profile-reference.md.
    hold: bool = False
    # card only: drop a leading "[SEVERITY]" and system name from the title,
    # which the card's header bar already shows.
    strip_prefix: bool = False
    # card only: what the card shows, filled in when a message is matched.
    card: CardContent | None = None
    color: tuple[int, int, int] = (255, 255, 255)
    systems: list[str] = field(default_factory=list)
    severity: list[str] = field(default_factory=list)


@dataclass
class Profile:
    """Loaded display profile with rules and color definitions."""

    rules: dict[str, DisplayConfig] = field(default_factory=dict)
    colors: dict[str, tuple[int, int, int]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Merge defaults with any custom colors
        merged = dict(DEFAULT_COLORS)
        merged.update(self.colors)
        self.colors = merged


def load_profile(path: str | Path, rules_expected: bool = True) -> Profile:
    """Load a display profile from a YAML file.

    A missing file is a warning where messages are matched against rules, and
    only a notice where nothing is (standalone): there the profile supplies
    nothing but colour names, and the built-in ones are the normal case (#107).
    """
    path = Path(path)
    if not path.exists():
        if rules_expected:
            logger.warning("profile not found at %s, using empty profile", path)
        else:
            logger.info("no profile at %s, using the default colours", path)
        return Profile()

    with open(path) as f:
        data = yaml.safe_load(f)

    if not data:
        return Profile()

    # Parse colors
    colors: dict[str, tuple[int, int, int]] = {}
    for name, rgb in data.get("colors", {}).items():
        if isinstance(rgb, list) and len(rgb) == 3:
            colors[name] = tuple(rgb)  # type: ignore[arg-type]

    # Parse display rules
    rules: dict[str, DisplayConfig] = {}
    for rule_name, rule_data in data.get("displayRules", {}).items():
        severity_list = rule_data.get("severity", [])
        # Normalize severity to lowercase
        severity_list = [s.lower() for s in severity_list]

        rules[rule_name] = DisplayConfig(
            kind=rule_data.get("kind", "text"),
            text=rule_data.get("text", ""),
            image=rule_data.get("image", ""),
            font=rule_data.get("font", "6x10.bdf"),
            duration=float(rule_data.get("duration", 5)),
            hold=bool(rule_data.get("hold", False)),
            strip_prefix=bool(rule_data.get("strip_prefix", False)),
            systems=rule_data.get("systems", []),
            severity=severity_list,
        )

    profile = Profile(rules=rules, colors=colors)
    logger.info("loaded profile with %d rules from %s", len(rules), path)
    return profile


def match_rule(profile: Profile, msg: Message) -> DisplayConfig | None:
    """Find the first matching display rule for a message.

    Matching logic:
    1. Iterate rules in order
    2. Check if message system matches rule systems (or wildcard "*")
    3. Check if message severity matches rule severity list
    4. First match wins
    """
    msg_system = msg.system.lower()
    msg_severity = msg.severity.lower()

    for rule_name, config in profile.rules.items():
        # Check system match
        system_match = "*" in config.systems or msg_system in [s.lower() for s in config.systems]
        if not system_match:
            continue

        # Check severity match
        severity_match = not config.severity or msg_severity in config.severity
        if not severity_match:
            continue

        logger.debug("matched rule '%s' for system=%s severity=%s", rule_name, msg_system, msg_severity)
        return _resolve_config(config, msg, profile)

    return None


def _resolve_config(config: DisplayConfig, msg: Message, profile: Profile) -> DisplayConfig:
    """Resolve a display config with Jinja2 templating and color lookup."""
    resolved = DisplayConfig(
        kind=config.kind,
        text=config.text,
        image=config.image,
        font=config.font,
        duration=config.duration,
        hold=config.hold,
        strip_prefix=config.strip_prefix,
        systems=config.systems,
        severity=config.severity,
    )

    # Render Jinja2 text template
    if resolved.text:
        try:
            template = _JINJA.from_string(resolved.text)
            resolved.text = template.render(
                title=msg.title,
                message=msg.message,
                severity=msg.severity,
                system=msg.system,
                author=msg.author,
                tags=msg.tags,
                url=msg.url,
                timestamp=msg.timestamp,
            )
        except jinja2.TemplateError:
            logger.exception("failed to render template for text: %s", resolved.text)

    # Resolve color from severity
    severity_key = msg.severity.lower()
    resolved.color = profile.colors.get(severity_key, DEFAULT_COLORS.get(severity_key, (255, 255, 255)))

    if resolved.kind.lower() == "card":
        # The rule's text, when it has one, is the card's scrolling line; the
        # message body otherwise.
        if not config.text:
            resolved.text = msg.message
        title = msg.title or msg.message
        if resolved.strip_prefix:
            title = strip_title_prefix(title, msg.severity, msg.system)
        resolved.card = CardContent(
            system=msg.system,
            title=title,
            message=resolved.text,
            author=msg.author,
            # The time the event happened, per its producer. A message without
            # a usable timestamp shows when the catcher received it instead:
            # an alert card with no time reads as a stale one.
            time=format_time(msg.timestamp) or datetime.now().strftime(CARD_TIME_FORMAT),
            tags=msg.tags,
        )

    return resolved


# ----------------------------------------------------------------- timestamps

CARD_TIME_FORMAT = "%H:%M"

# More than six fractional digits (Go's RFC3339Nano) is more than
# datetime.fromisoformat takes before Python 3.12.
_FRACTION = re.compile(r"(\.\d{6})\d+")


def parse_timestamp(value: str) -> datetime | None:
    """An RFC 3339 / ISO 8601 timestamp as an aware local datetime, or None.

    A timestamp without an offset is taken as local time. Local means the
    catcher's: the TZ environment variable, or the host's zone.
    """
    if not value or not value.strip():
        return None
    text = value.strip()
    if text[-1] in "Zz":
        text = text[:-1] + "+00:00"
    text = _FRACTION.sub(r"\1", text)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.astimezone()


def format_time(value: str, fmt: str = CARD_TIME_FORMAT) -> str:
    """``value`` (RFC 3339) in local time as ``fmt``; empty when unparseable."""
    parsed = parse_timestamp(value)
    return parsed.strftime(fmt) if parsed is not None else ""


def _localtime_filter(value: str, fmt: str = CARD_TIME_FORMAT) -> str:
    """Jinja filter: ``{{ timestamp | localtime }}`` → ``14:05``.

    An unparseable value is passed through as it is: in a template the raw
    string says more than nothing does.
    """
    return format_time(str(value or ""), fmt) or str(value or "")


# One environment for every rule. jinja2.Template() used a shared default
# environment with these same settings; this one only adds the filter.
# Not autoescaped: the output is pixels on the panel, not HTML — escaping would
# put "&amp;" on the matrix. The simulator escapes what it puts in the page.
_JINJA = jinja2.Environment(autoescape=False)  # nosec B701 - renders panel text, not HTML
_JINJA.filters["localtime"] = _localtime_filter
