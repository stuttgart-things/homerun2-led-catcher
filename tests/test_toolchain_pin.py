"""The ruff CI uses and the ruff a contributor installs must be one version.

`ruff format` output changes between minor releases: 0.8.6 wraps a long assert
message one way, 0.15 another. With a range in pyproject.toml and a fixed
version in CI, the two drift apart and Format-Check rejects a file the
contributor just formatted with the command the README tells them to run (#61).

These tests fail the moment the two pins stop matching, in `task precommit` —
before a push, not after one.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = REPO_ROOT / "pyproject.toml"
BUILD_TEST_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "build-test.yaml"

_RUFF_VERSION_INPUT = re.compile(r"^\s*ruff-version:\s*[\"']?([^\"'\s]+)[\"']?\s*$", re.MULTILINE)


def pyproject_ruff_requirement() -> str:
    """The `ruff` entry of the dev extra, as written."""
    data = tomllib.loads(PYPROJECT.read_text())
    dev = data["project"]["optional-dependencies"]["dev"]
    ruff = [entry for entry in dev if re.match(r"^ruff\b", entry)]
    assert len(ruff) == 1, f"expected exactly one ruff entry in the dev extra, got {ruff}"
    return ruff[0]


def workflow_ruff_versions() -> list[str]:
    """Every `ruff-version:` input handed to a reusable workflow."""
    return _RUFF_VERSION_INPUT.findall(BUILD_TEST_WORKFLOW.read_text())


def test_the_dev_extra_pins_ruff_exactly():
    requirement = pyproject_ruff_requirement()
    assert requirement.startswith("ruff=="), (
        f"the dev extra must pin ruff exactly, not {requirement!r} — a range installs whatever "
        "ruff is current, which formats differently from the version CI runs (#61)"
    )


def test_ci_pins_the_same_ruff_as_the_dev_extra():
    pinned = pyproject_ruff_requirement().removeprefix("ruff==").strip()
    in_ci = workflow_ruff_versions()

    assert in_ci, f"no ruff-version input found in {BUILD_TEST_WORKFLOW.name}"
    assert set(in_ci) == {pinned}, (
        f"pyproject.toml pins ruff {pinned}, {BUILD_TEST_WORKFLOW.name} runs {sorted(set(in_ci))} — "
        "bump both together, or Format-Check fails on correctly formatted code (#61)"
    )
