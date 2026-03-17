"""Profile/rules engine for display mode routing.

Maps (system, severity) combinations to LED display configurations
using YAML profiles with first-match semantics.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import jinja2
import yaml

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

    kind: str = "text"  # static, text, ticker, image, gif
    text: str = ""
    image: str = ""
    font: str = "myfont.bdf"
    duration: float = 5.0
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


def load_profile(path: str | Path) -> Profile:
    """Load a display profile from a YAML file."""
    path = Path(path)
    if not path.exists():
        logger.warning("profile not found at %s, using empty profile", path)
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
            font=rule_data.get("font", "myfont.bdf"),
            duration=float(rule_data.get("duration", 5)),
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
        systems=config.systems,
        severity=config.severity,
    )

    # Render Jinja2 text template
    if resolved.text:
        try:
            template = jinja2.Template(resolved.text)
            resolved.text = template.render(
                title=msg.title,
                message=msg.message,
                severity=msg.severity,
                system=msg.system,
                author=msg.author,
                tags=msg.tags,
                url=msg.url,
            )
        except jinja2.TemplateError:
            logger.exception("failed to render template for text: %s", resolved.text)

    # Resolve color from severity
    severity_key = msg.severity.lower()
    resolved.color = profile.colors.get(severity_key, DEFAULT_COLORS.get(severity_key, (255, 255, 255)))

    return resolved
