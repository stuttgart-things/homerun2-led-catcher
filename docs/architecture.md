# Architecture

## Overview

homerun2-led-catcher is a Python microservice that shows homerun2 notifications on a
64x64 RGB LED matrix (via rpi-rgb-led-matrix). It has two inputs:

- **Redis Streams:** messages published by the homerun2 pitchers, routed to a
  display by a YAML profile
- **the `/display` HTTP API:** a caller says exactly what goes on the panel, with no
  message and no rule in between

An embedded HTMX web simulator draws what the panel draws, for development, demos
and checking the hardware against a reference.

## Data flow

```
Redis Stream ──► RedisConsumer ──► CaughtMessage
                 (XREADGROUP,          │
                  JSON.GET payload,    ├──► log_handler  structured JSON log
                  XACK)                ├──► led_handler  profile rule ─┐
                                       └──► web_handler  profile rule ─┼──► EventTracker
                                                                       │
POST /display ──► token, rate limit, validation ──► DisplayConfig ─────┤
DELETE /display ──────────────────────────────────► blank ─────────────┤
                                                                       ▼
                                                  DisplayWorker (one thread, owns the panel)
                                                        │
                                                        ▼
                                              display modes ──► MatrixDisplay ──► panel
                                              static, text,     (rpi-rgb-led-matrix,
                                              ticker, image,     no-op without it)
                                              gif, score

EventTracker ──► SSE /api/events/stream ──► browser: timeline + canvas
                                             canvas draws with the panel's inputs:
                                             /api/preview/text  (BDF glyphs)
                                             /api/preview/image (prepared frames)
```

Every input ends up as a `DisplayConfig`, which the `DisplayWorker` takes and
shows. Messages and API calls follow the same rules on the panel, in the logs and
in the simulator.

## Components

| Package | Description |
|---------|-------------|
| `consumer/` | Async Redis Streams consumer: consumer groups, `JSON.GET` payload resolution, retry at startup, runtime stream switching |
| `handlers/` | `log_handler`, `led_handler`, `/healthz`, `/streams` (`control.py`), `/display` (`display_api.py`) |
| `models/` | `Message` and `CaughtMessage` dataclasses |
| `config/` | Environment variable loading (`Config`, `PanelConfig`, `ApiConfig`), JSON log formatter |
| `profile/` | YAML profile loading, first-match rule matching, Jinja2 templating, severity colours |
| `display/` | `worker.py` (the thread that owns the panel), `modes.py` (display modes), `matrix.py` (rpi-rgb-led-matrix wrapper), `bdf.py` (font metrics and glyphs), `frames.py` (prepared image/GIF frames), `score.py` (scoreboard) |
| `web/` | FastAPI HTMX simulator, SSE, `EventTracker`, `web_handler`, preview routes for the canvas, Panel control form |
| `tools/` | `led-catcher-publish`: test producer that writes into the stream like the pitchers do |

## The display worker

Displaying is slow on purpose: a message stays on the panel for its `duration`, and a
held one (`hold: true`) until something replaces it. That work runs on **one** thread
that owns the panel, fed through a **single slot where the newest display wins**:

| On the panel | A new display arrives | Result |
|---|---|---|
| nothing | anything | shown at once |
| held | anything | replaces it at once |
| finite | anything | waits until the current one has had its full `duration` |
| finite | several | only the newest is shown next |

`DELETE /display` is the exception: it blanks the panel immediately. The consumer and
the API only write the slot and return, so neither the stream nor `/healthz` waits
for the panel (#54).

## Operating modes

| `LED_MODE` | Inputs | Outputs | Use case |
|------|--------|---------|----------|
| `led` | Redis, `/display` | panel | Raspberry Pi, no browser UI |
| `web` | Redis, `/display` | simulator | development, Kubernetes, demos |
| `full` | Redis, `/display` | panel + simulator | Raspberry Pi with the simulator for comparison |
| `standalone` | `/display` only, **no Redis** | panel + simulator | hardware tests, a panel driven by curl, Home Assistant or scripts |

The `/display` write endpoints exist only when `LED_API_TOKEN` is set. In
`standalone` the process exits non-zero if the web server or the display worker
stops, and `/healthz` answers 503 when the worker is dead, so a broken instance
restarts instead of staying up and doing nothing (#65).

## Profile/rules engine

Messages are routed to display modes by a YAML profile with first-match semantics:

1. Iterate rules in order
2. Match the message system against the rule's systems (or wildcard `*`)
3. Match the message severity against the rule's severity list
4. First match → resolve the display config (Jinja2 templating, severity colour)
5. No match → not displayed (logged at `info`, listed in the simulator timeline)

`/display` bypasses the profile; only its `colors:` block is used, for named colours.
See [Profile Reference](profile-reference.md).

## The simulator is a reference

The canvas does not draw its own picture of an event. It renders each display the
way `display/modes.py` does, from the panel's own inputs: the BDF glyph bitmaps of
the configured font, and image/GIF frames prepared by the same `load_animation` the
panel plays. Replacement follows the worker's rules. So the canvas shows what the
panel *should* show, and a difference between them is a hardware finding (#83).

## CI/CD pipeline

```
Pre-commit hook (local, task precommit)
    │
    ├─→ ruff check (lint)
    ├─→ ruff format --check
    └─→ pytest (unit tests, no Redis or hardware needed)

GitHub Actions (remote), on Python 3.11 and 3.14
    │
    ├─→ Dagger: python lint
    ├─→ Dagger: python format-check
    ├─→ Dagger: python test
    ├─→ Dagger: python security-scan (bandit)
    └─→ Dagger: docker build → push ttl.sh → trivy scan
```

3.11 is the Pi (Raspberry Pi OS Bookworm) and the `requires-python` floor; 3.14 is
the container image. `tests/test_toolchain_pin.py` fails when the Dockerfile, the CI
matrix, the Taskfile or the ruff pins drift apart.

## Deployment

- **Raspberry Pi:** native install in a venv with the matrix library, run as a
  systemd service, see [Raspberry Pi Deployment](raspberry-pi-deployment.md) and
  [Run as a service](raspberry-pi-service.md)
- **Kubernetes:** KCL renders the Deployment (rolling update, security hardening,
  health probes), Service (ClusterIP), ConfigMaps, Secrets (Redis password,
  `/display` token), ServiceAccount and an optional HTTPRoute (Gateway API). The
  namespace is not part of the manifests; see [Deployment](deployment.md)
