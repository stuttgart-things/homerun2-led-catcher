"""The OpenAPI description of the HTTP API, for Backstage and for readers (#98).

The running service keeps `/openapi.json` switched off, so nothing new is
exposed on the panel's port. The spec is generated from the same routers
instead, and committed as `docs/openapi.yaml`:

    python -m led_catcher.openapi > docs/openapi.yaml     # or: task openapi

`tests/test_openapi.py` fails when the committed file is stale.

Only the machine-facing API is described: `/display`, `/streams` and
`/healthz`. The simulator's own routes (the HTML page, its partials, SSE and
the preview endpoints) are an implementation detail of the page.
"""

from __future__ import annotations

import sys

import yaml
from fastapi import FastAPI

from led_catcher.config.settings import ApiConfig, Config, RedisConfig
from led_catcher.consumer import RedisConsumer
from led_catcher.handlers.control import create_control_router
from led_catcher.handlers.display_api import create_display_router
from led_catcher.handlers.health import healthz

API_VERSION = "1"
"""Bumped when the HTTP API changes incompatibly, not with every release."""

DESCRIPTION = """\
HTTP API of homerun2-led-catcher, the homerun2 service that shows notifications
on a 64x64 RGB LED matrix.

- **`/display`** puts something on the panel directly, with no message and no
  profile rule. `POST` and `DELETE` need `Authorization: Bearer <LED_API_TOKEN>`;
  without the token set they are not registered at all. Writes are rate-limited
  across all callers (`LED_API_RATE_LIMIT`, default 30 a minute).
- **`/streams`** switches the Redis streams the service consumes, at runtime.
  Not available in `LED_MODE=standalone`.
- **`/healthz`** is liveness plus build info.

Guides: [Display API](https://stuttgart-things.github.io/homerun2-led-catcher/display-api/),
[Testing with curl](https://stuttgart-things.github.io/homerun2-led-catcher/testing-with-curl/).
"""


def build_app() -> FastAPI:
    """An app with every documented route registered, for spec generation only."""
    app = FastAPI(title="homerun2-led-catcher", version=API_VERSION, description=DESCRIPTION)
    # A token, so the write endpoints are registered and appear in the spec.
    app.include_router(create_display_router(None, ApiConfig(token="spec-only")))
    # Never started: the router only needs something to hand requests to.
    app.include_router(create_control_router(RedisConsumer(Config(redis=RedisConfig()), [])))
    app.add_api_route("/healthz", healthz, methods=["GET"], summary="Liveness and build info", tags=["health"])
    return app


class _Dumper(yaml.SafeDumper):
    """Multi-line strings as `|` blocks, so the descriptions read like text in review."""


def _str(dumper: yaml.SafeDumper, value: str) -> yaml.ScalarNode:
    style = "|" if "\n" in value else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_Dumper.add_representer(str, _str)


def render() -> str:
    """The spec as YAML, key order as FastAPI produces it."""
    return yaml.dump(build_app().openapi(), Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=100)


if __name__ == "__main__":
    sys.stdout.write(render())
