"""Toolchain pins that must agree: ruff everywhere, and the Pythons CI tests.

The ruff CI uses and the ruff a contributor installs must be one version.

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

import yaml

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


# ---- `task ci` runs what CI runs (#88) ---------------------------------------

TASKFILE = REPO_ROOT / "Taskfile.yaml"
DOCKERFILE = REPO_ROOT / "Dockerfile"


def taskfile_vars() -> dict:
    return yaml.safe_load(TASKFILE.read_text())["vars"]


def test_task_ci_pins_the_same_ruff_as_the_dev_extra():
    pinned = pyproject_ruff_requirement().removeprefix("ruff==").strip()
    assert str(taskfile_vars()["RUFF_VERSION"]) == pinned, (
        "Taskfile.yaml RUFF_VERSION drifted from pyproject.toml — `task ci-format-check` would "
        "format with a different ruff than CI (#61)"
    )


# ---- the Python versions tested are the ones that run (#88) -------------------


def dockerfile_python() -> str:
    match = re.search(r"^FROM\s+python:(\S+)", DOCKERFILE.read_text(), re.MULTILINE)
    assert match, "no `FROM python:` line in the Dockerfile"
    return match.group(1)


def ci_python_matrix() -> list[str]:
    workflow = yaml.safe_load(BUILD_TEST_WORKFLOW.read_text())
    return [str(v) for v in workflow["jobs"]["python-validation"]["strategy"]["matrix"]["python-version"]]


def requires_python_floor() -> str:
    spec = tomllib.loads(PYPROJECT.read_text())["project"]["requires-python"]
    match = re.fullmatch(r">=\s*(3\.\d+)", spec.strip())
    assert match, f"expected requires-python as '>=3.x', got {spec!r}"
    return match.group(1)


def test_ci_tests_the_python_the_image_runs():
    assert dockerfile_python() in ci_python_matrix(), (
        f"the image runs python:{dockerfile_python()}, CI tests {ci_python_matrix()} — "
        "the Dockerfile moved without CI (#88)"
    )


def test_ci_tests_the_lowest_python_the_package_accepts():
    floor = requires_python_floor()
    assert f"{floor}-slim" in ci_python_matrix(), (
        f"requires-python allows {floor}, but CI never runs it — that is the Pi's version (#88)"
    )


def test_task_ci_tests_the_same_pythons_as_ci():
    tv = taskfile_vars()
    assert {str(tv["PYTHON_VERSION"]), str(tv["PYTHON_VERSION_FLOOR"])} == set(ci_python_matrix())


def test_ruff_targets_the_python_floor():
    target = tomllib.loads(PYPROJECT.read_text())["tool"]["ruff"]["target-version"]
    assert target == "py" + requires_python_floor().replace(".", ""), (
        f"ruff target-version {target} does not match requires-python >= {requires_python_floor()}"
    )
