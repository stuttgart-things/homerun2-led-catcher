"""Tests for the test-message publisher (no Redis required)."""

import json

import pytest

from led_catcher.tools.publish import (
    DEMO_STEPS,
    SEVERITIES,
    _module_names,
    build_payload,
    main,
    parse_args,
)


def test_defaults_come_from_env(monkeypatch):
    monkeypatch.setenv("REDIS_STREAM", "custom-stream")
    args = parse_args([])
    assert args.stream == "custom-stream"
    assert args.severity == "info"
    assert args.count == 1


def test_explicit_stream_wins_over_env(monkeypatch):
    monkeypatch.setenv("REDIS_STREAM", "from-env")
    args = parse_args(["--stream", "from-flag"])
    assert args.stream == "from-flag"


def test_payload_uses_camel_case_keys():
    args = parse_args(["--system", "scale", "--severity", "warning", "--title", "t", "--message", "m"])
    payload = build_payload(args)
    assert payload["system"] == "scale"
    assert payload["severity"] == "warning"
    assert payload["title"] == "t"
    assert payload["message"] == "m"
    # Keys must match the Go homerun.Message JSON, which the consumer maps back
    assert set(payload) == {"title", "message", "severity", "author", "timestamp", "system", "tags", "url"}


def test_payload_generates_timestamp():
    payload = build_payload(parse_args([]))
    assert payload["timestamp"].endswith("+00:00")


def test_payload_keeps_explicit_timestamp():
    payload = build_payload(parse_args(["--timestamp", "2026-01-01T00:00:00Z"]))
    assert payload["timestamp"] == "2026-01-01T00:00:00Z"


def test_payload_is_json_serializable():
    json.dumps(build_payload(parse_args([])))


def test_invalid_severity_rejected():
    with pytest.raises(SystemExit):
        parse_args(["--severity", "critical"])


def test_demo_steps_cover_every_severity_path():
    severities = {step.severity for step in DEMO_STEPS}
    assert severities <= set(SEVERITIES)
    assert {"info", "warning", "error", "success"} <= severities


def test_module_names_handles_dict_and_pair_shapes():
    assert _module_names([{"name": "ReJSON", "ver": 20609}]) == {"rejson"}
    assert _module_names([["name", "ReJSON", "ver", 20609]]) == {"rejson"}
    assert _module_names(None) == set()


def _no_client(*_args, **_kwargs):
    raise AssertionError("--dry-run must not open a Redis client")


def test_demo_dry_run_prints_every_step_without_redis(monkeypatch, capsys):
    monkeypatch.setattr("led_catcher.tools.publish._connect", _no_client)
    assert main(["--demo", "--dry-run"]) == 0
    out = capsys.readouterr().out
    payloads = [json.loads(block) for block in _json_blocks(out)]
    assert len(payloads) == len(DEMO_STEPS)
    for step, payload in zip(DEMO_STEPS, payloads, strict=True):
        assert (payload["system"], payload["severity"], payload["title"]) == (step.system, step.severity, step.title)
        assert f"expect {step.expect}" in out


def test_dry_run_prints_single_payload_without_redis(monkeypatch, capsys):
    monkeypatch.setattr("led_catcher.tools.publish._connect", _no_client)
    assert main(["--dry-run", "--title", "hi"]) == 0
    assert json.loads(capsys.readouterr().out)["title"] == "hi"


def _json_blocks(out: str) -> list[str]:
    """Split the dry-run output into the pretty-printed JSON objects it contains."""
    blocks, current = [], []
    for line in out.splitlines():
        if line == "{":
            current = [line]
        elif current:
            current.append(line)
            if line == "}":
                blocks.append("\n".join(current))
                current = []
    return blocks
