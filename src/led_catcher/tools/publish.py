"""Publish test messages into the Redis stream the catcher consumes.

Mirrors what homerun2-omni-pitcher does in production, so the display can be
exercised on real hardware without the rest of the platform:

    JSON.SET <object-id> $ <message payload>     # RedisJSON document
    XADD <stream> * messageID <object-id>        # stream entry pointing at it

Requires a Redis with the RedisJSON module (redis-stack) — the consumer
resolves every entry via JSON.GET.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import redis

from led_catcher.config import load_config

SEVERITIES = ("debug", "info", "warning", "error", "success")


@dataclass
class DemoStep:
    """One message in the --demo walkthrough, with the mode it should trigger."""

    expect: str
    system: str
    severity: str
    title: str
    message: str


# Exercises every display mode in the shipped profile (see tests/profile.yaml).
DEMO_STEPS: tuple[DemoStep, ...] = (
    DemoStep("text (scroll)", "demo", "info", "hello from the pi", "scrolling info line"),
    DemoStep("static", "scale", "info", "weight", "WEIGHT: 1234"),
    DemoStep("text (scroll)", "demo", "success", "build green", "pipeline succeeded"),
    DemoStep("text (scroll)", "demo", "warning", "disk 85%", "node-01 running low"),
    DemoStep("gif / image", "github", "error", "build failed", "job #42 exited 1"),
)


def build_payload(args: argparse.Namespace) -> dict[str, str]:
    """Assemble a homerun message document (camelCase, as the Go struct emits)."""
    return {
        "title": args.title,
        "message": args.message,
        "severity": args.severity,
        "author": args.author,
        "timestamp": args.timestamp or datetime.now(timezone.utc).isoformat(),
        "system": args.system,
        "tags": args.tags,
        "url": args.url,
    }


def publish(client: redis.Redis, stream: str, payload: dict[str, str], key_prefix: str) -> tuple[str, str]:
    """Write the JSON document and the stream entry pointing at it."""
    object_id = f"{key_prefix}:{uuid.uuid4().hex[:12]}"
    client.execute_command("JSON.SET", object_id, "$", json.dumps(payload))
    entry_id = client.xadd(stream, {"messageID": object_id})
    return object_id, entry_id


def _connect(cfg) -> redis.Redis:
    return redis.Redis(
        host=cfg.redis.addr,
        port=cfg.redis.port,
        password=cfg.redis.password or None,
        decode_responses=True,
    )


def _module_names(modules) -> set[str]:
    """Module names from MODULE LIST, which redis-py returns as dicts or flat pairs."""
    names: set[str] = set()
    for module in modules or []:
        if isinstance(module, dict):
            names.add(str(module.get("name", "")).lower())
        elif isinstance(module, (list, tuple)) and len(module) > 1:
            names.add(str(module[1]).lower())
    return names


def _check_json_module(client: redis.Redis) -> None:
    """Fail early and clearly when RedisJSON is missing."""
    try:
        modules = client.module_list()
    except redis.RedisError:
        return  # MODULE LIST disabled (managed Redis) — let JSON.SET decide
    if not any("json" in n for n in _module_names(modules)):
        sys.exit(
            "error: this Redis has no RedisJSON module — the catcher resolves every\n"
            "       stream entry with JSON.GET. Use redis-stack, e.g.:\n"
            "         docker run -d -p 6379:6379 redis/redis-stack-server:latest"
        )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    cfg = load_config()
    parser = argparse.ArgumentParser(
        prog="led-catcher-publish",
        description="Publish test messages into the Redis stream (hardware smoke testing).",
        epilog=(
            "Redis connection comes from REDIS_ADDR / REDIS_PORT / REDIS_PASSWORD, the same env vars the catcher uses."
        ),
    )
    parser.add_argument("--stream", default=cfg.redis.stream, help="stream to publish to (default: %(default)s)")
    parser.add_argument("--system", default="demo", help="source system, matched by profile rules")
    parser.add_argument("--severity", default="info", choices=SEVERITIES, help="message severity")
    parser.add_argument("--title", default="test message", help="message title")
    parser.add_argument("--message", default="hello from led-catcher-publish", help="message body")
    parser.add_argument("--author", default="led-catcher-publish", help="message author")
    parser.add_argument("--tags", default="test", help="comma-separated tags")
    parser.add_argument("--url", default="", help="optional URL")
    parser.add_argument("--timestamp", default="", help="ISO timestamp (default: now, UTC)")
    parser.add_argument("--count", type=int, default=1, help="how many copies to send (default: %(default)s)")
    parser.add_argument("--interval", type=float, default=1.0, help="seconds between messages (default: %(default)s)")
    parser.add_argument("--key-prefix", default="led-catcher-test", help="prefix for the RedisJSON keys")
    parser.add_argument("--demo", action="store_true", help="walk through every display mode instead of one message")
    parser.add_argument("--dry-run", action="store_true", help="print the payload, write nothing")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    cfg = load_config()

    if args.dry_run:
        print(json.dumps(build_payload(args), indent=2))
        return 0

    client = _connect(cfg)
    try:
        client.ping()
    except redis.RedisError as exc:
        sys.exit(f"error: cannot reach Redis at {cfg.redis.addr}:{cfg.redis.port}: {exc}")
    _check_json_module(client)

    print(f"publishing to {cfg.redis.addr}:{cfg.redis.port} stream={args.stream}")

    if args.demo:
        for step in DEMO_STEPS:
            args.system = step.system
            args.severity = step.severity
            args.title = step.title
            args.message = step.message
            object_id, entry_id = publish(client, args.stream, build_payload(args), args.key_prefix)
            route = f"{step.system}/{step.severity}"
            print(f"  {entry_id}  {route:<16} → expect {step.expect:<14} ({object_id})")
            time.sleep(max(args.interval, 0.0))
        print(f"demo done — {len(DEMO_STEPS)} messages sent")
        return 0

    for i in range(args.count):
        object_id, entry_id = publish(client, args.stream, build_payload(args), args.key_prefix)
        print(f"  {entry_id}  {args.system}/{args.severity} ({object_id})")
        if i < args.count - 1:
            time.sleep(max(args.interval, 0.0))

    print(f"sent {args.count} message(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
