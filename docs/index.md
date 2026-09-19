# homerun2-led-catcher

RGB LED matrix catcher for the [homerun2](https://github.com/stuttgart-things) ecosystem.

Shows homerun2 notifications from Redis Streams on a 64x64 RGB LED matrix. The panel can also be driven directly over HTTP, and an embedded web simulator draws what the panel draws.

## Features

- **Redis Streams consumer** with consumer groups (same pattern as core-catcher, light-catcher)
- **Profile-based display routing** — YAML rules map (system, severity) to display modes
- **6 display modes** — static text, scrolling text, ticker, image, animated GIF, table-tennis scoreboard
- **`/display` API** — put anything on the panel with `curl`, no Redis needed ([Testing with curl](testing-with-curl.md))
- **HTMX web simulator** — 64x64 canvas that draws what the panel draws (same fonts, frames and timing), with real-time SSE updates
- **Configurable modes** — `led` (hardware), `web` (simulator), `full` (both), `standalone` (panel + simulator driven by the API, no Redis)
- **KCL deployment** — Kubernetes manifests with security hardening

## Quick Start

```bash
pip install -e ".[dev]"
task setup-precommit
LED_MODE=web LOG_FORMAT=text python -m led_catcher
```

Open [http://localhost:8080](http://localhost:8080) for the web simulator.
Without Redis: `task run-standalone`, then [Testing with curl](testing-with-curl.md).

## Where to go

| I want to… | Page |
|---|---|
| understand how it fits together | [Architecture](architecture.md) |
| put something on the panel right now | [Testing with curl](testing-with-curl.md) |
| set up a Raspberry Pi with a panel | [Hardware Setup](hardware-setup.md), then [Install](raspberry-pi-deployment.md) |
| run it as a service, or install with Ansible | [Run as a service](raspberry-pi-service.md) |
| test, troubleshoot or update a Pi | [Operations](raspberry-pi-operations.md) |
| look up the HTTP API or the profile format | [Display API](display-api.md), [Profile Reference](profile-reference.md) |
| deploy to Kubernetes | [Kubernetes](deployment.md) |
| work on the code | [Development](development.md) |

## Related Services

| Service | Role |
|---------|------|
| [homerun2-omni-pitcher](https://github.com/stuttgart-things/homerun2-omni-pitcher) | HTTP producer |
| [homerun2-core-catcher](https://github.com/stuttgart-things/homerun2-core-catcher) | Core consumer (log/CLI/web) |
| [homerun2-light-catcher](https://github.com/stuttgart-things/homerun2-light-catcher) | WLED light consumer |
| **homerun2-led-catcher** | LED matrix consumer |
