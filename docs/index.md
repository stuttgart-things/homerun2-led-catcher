# homerun2-led-catcher

RGB LED matrix catcher for the [homerun2](https://github.com/stuttgart-things) ecosystem.

Consumes messages from Redis Streams and displays them on a 64x64 RGB LED matrix with an embedded HTMX web simulator for development and demos.

## Features

- **Redis Streams consumer** with consumer groups (same pattern as core-catcher, light-catcher)
- **Profile-based display routing** — YAML rules map (system, severity) to display modes
- **5 display modes** — static text, scrolling text, ticker, image, animated GIF
- **HTMX web simulator** — canvas-based 64x64 LED grid with real-time SSE updates
- **Configurable modes** — `led` (hardware), `web` (simulator), `full` (both)
- **KCL deployment** — Kubernetes manifests with security hardening

## Quick Start

```bash
pip install -e ".[dev]"
task setup-precommit
LED_MODE=web LOG_FORMAT=text python -m led_catcher
```

Open [http://localhost:8080](http://localhost:8080) for the web simulator.

## Related Services

| Service | Role |
|---------|------|
| [homerun2-omni-pitcher](https://github.com/stuttgart-things/homerun2-omni-pitcher) | HTTP producer |
| [homerun2-core-catcher](https://github.com/stuttgart-things/homerun2-core-catcher) | Core consumer (log/CLI/web) |
| [homerun2-light-catcher](https://github.com/stuttgart-things/homerun2-light-catcher) | WLED light consumer |
| **homerun2-led-catcher** | LED matrix consumer |
