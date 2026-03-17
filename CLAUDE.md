# CLAUDE.md

## Project

homerun2-led-catcher — Python microservice that consumes messages from Redis Streams and displays them on a 64x64 RGB LED matrix (via rpi-rgb-led-matrix) with an embedded HTMX web simulator for development and demos.

## Tech Stack

- **Language**: Python 3.11+
- **Consumer**: Redis Streams via `redis-py` async (consumer groups)
- **LED Matrix**: `rpi-rgb-led-matrix` Python bindings (optional, for Raspberry Pi)
- **Web**: FastAPI + HTMX + SSE (sse-starlette) for simulator UI
- **Build**: Dockerfile (multi-stage), no ko (Python project)
- **CI**: Dagger modules (`dagger/main.go`), Taskfile
- **Deploy**: KCL manifests (`kcl/`), Kubernetes
- **Infra**: GitHub Actions, semantic-release, renovate

## Git Workflow

**Branch-per-issue with PR and merge.** Every change gets its own branch, PR, and merge to main.

### Branch naming

- `fix/<issue-number>-<short-description>` for bugs
- `feat/<issue-number>-<short-description>` for features
- `test/<issue-number>-<short-description>` for test-only changes
- `chore/<issue-number>-<short-description>` for infra/CI changes

### Commit messages

- Use conventional commits: `fix:`, `feat:`, `test:`, `chore:`, `docs:`
- End with `Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>` when Claude authored
- Include `Closes #<issue-number>` to auto-close issues

## Code Conventions

- Python package with `src/` layout (`src/led_catcher/`)
- Config via environment variables, loaded once at startup (`config/settings.py`)
- Tests: `pytest` — unit tests must not require Redis; integration tests run via Dagger with Redis service
- Pluggable message handlers: `log_handler`, `led_handler`, `web_handler`
- Logging: Python `logging` with JSON formatter (matching Go slog output)
- `rgbmatrix` import is optional — graceful no-op when not on Raspberry Pi

## Architecture

```
Redis Stream ──► RedisConsumer ──┬──► log_handler (structured JSON)
                                 ├──► led_handler (RGB LED matrix)
                                 └──► web_handler (HTMX simulator)
                                           │
                                     ┌─────┘
                                     ▼
                               Profile YAML
                               (system + severity → display mode)
                                     │
                              ┌──────┼──────┐
                              ▼      ▼      ▼
                           LED    HTMX    Both
                         Matrix   Web    (full)
```

### Components

| Component | Description |
|-----------|-------------|
| `consumer/` | Redis Stream consumer with consumer groups |
| `handlers/` | Log handler, health endpoint, LED/web handlers (future) |
| `models/` | Message and CaughtMessage dataclasses |
| `config/` | Env var loading, logging setup |
| `display/` | LED matrix display modes (future, Milestone 2) |
| `profile/` | YAML profile loading + rule matching (future, Milestone 2) |
| `web/` | HTMX simulator with SSE (future, Milestone 3) |

## Key Paths

- `src/led_catcher/__main__.py` — entrypoint, signal handling, handler composition
- `src/led_catcher/consumer/redis_consumer.py` — RedisConsumer with JSON.GET payload resolution
- `src/led_catcher/handlers/log_handler.py` — severity-aware structured logging
- `src/led_catcher/handlers/health.py` — FastAPI health endpoint
- `src/led_catcher/config/settings.py` — Config dataclass, env loading, JSON log formatter
- `src/led_catcher/models/message.py` — Message + CaughtMessage dataclasses

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `REDIS_ADDR` | `localhost` | Redis host |
| `REDIS_PORT` | `6379` | Redis port |
| `REDIS_PASSWORD` | *(empty)* | Redis password |
| `REDIS_STREAM` | `messages` | Redis stream to consume |
| `CONSUMER_GROUP` | `homerun2-led-catcher` | Consumer group name |
| `CONSUMER_NAME` | hostname | Consumer name within group |
| `LED_MODE` | `full` | Operating mode: `led`, `web`, `full` |
| `HEALTH_PORT` | `8080` | Health/web server port |
| `PROFILE_PATH` | `profile.yaml` | Path to display rules YAML |
| `LOG_FORMAT` | `json` | Log format: `json` or `text` |
| `LOG_LEVEL` | `info` | Log level: `debug`, `info`, `warning`, `error` |
| `VERSION` | `dev` | Build version (injected at build time) |
| `COMMIT` | `unknown` | Git commit (injected at build time) |
| `DATE` | `unknown` | Build date (injected at build time) |

## Testing

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Unit tests (no Redis needed)
pytest tests/ -v

# Lint
ruff check src/ tests/

# Run locally (web mode, no hardware)
LED_MODE=web LOG_FORMAT=text python -m led_catcher

# Build Docker image
docker build -t homerun2-led-catcher:local .

# Task shortcuts
task install
task lint
task test
task run
```

## Reference Projects

- `homerun2-light-catcher` — sibling Go consumer (WLED lights, same patterns)
- `homerun2-core-catcher` — sibling Go consumer (log/CLI/web modes)
- `homerun2-omni-pitcher` — sibling Go producer service
- `homerun-matrix-catcher` — original Python project being replaced
