# Development

## Setup

```bash
git clone https://github.com/stuttgart-things/homerun2-led-catcher.git
cd homerun2-led-catcher

# Install with dev dependencies
pip install -e ".[dev]"

# Install pre-commit hook
task setup-precommit
```

## Running Locally

```bash
# Web simulator mode (no hardware needed)
task run

# Or manually
LED_MODE=web LOG_FORMAT=text REDIS_ADDR=localhost python -m led_catcher
```

The web simulator is available at `http://localhost:8080`.
Health endpoint at `http://localhost:8080/healthz`.

### With Redis (port-forward)

```bash
# Port-forward to cluster Redis
KUBECONFIG=~/.kube/movie-scripts \
  kubectl port-forward svc/redis-stack 6379:6379 -n homerun2-flux &

# Run with real messages
LED_MODE=web LOG_FORMAT=text \
  REDIS_ADDR=localhost REDIS_PASSWORD='<password>' \
  python -m led_catcher
```

## Quality Checks

### Pre-commit (runs automatically on git commit)

```bash
task precommit    # lint + format-check + test
```

### Individual Checks

```bash
task lint           # ruff check
task format         # ruff format (auto-fix)
task format-check   # ruff format --check
task test           # pytest
```

### Full CI Pipeline (Dagger, same as GitHub Actions)

```bash
task ci               # lint + format-check + test + security-scan
task ci-docker-build  # build Docker image
task ci-docker-push   # build + push to ttl.sh
task ci-trivy-scan    # build + push + trivy vulnerability scan
```

## Project Structure

```
src/led_catcher/
├── __init__.py
├── __main__.py          # Entry point, handler composition
├── config/
│   └── settings.py      # Env vars, logging setup
├── consumer/
│   └── redis_consumer.py # Redis Streams consumer
├── handlers/
│   ├── health.py        # /healthz endpoint
│   ├── log_handler.py   # Structured logging
│   └── led_handler.py   # LED display handler
├── models/
│   └── message.py       # Message + CaughtMessage
├── profile/
│   └── engine.py        # YAML rules, Jinja2
├── display/
│   ├── matrix.py        # rpi-rgb-led-matrix wrapper
│   └── modes.py         # static, scroll, ticker, image, gif
└── web/
    ├── app.py           # FastAPI HTMX app
    ├── events.py        # EventTracker ring buffer
    ├── handler.py       # Web event recorder
    └── templates/
        └── index.html   # HTMX simulator UI
```

## Git Workflow

1. Create branch: `git checkout -b feat/<issue>-<desc>`
2. Make changes, commits are gated by pre-commit hook
3. Push: `git push -u origin <branch>`
4. Create PR: `gh pr create --base main`
5. CI runs automatically (lint, format, test, security, docker build+scan)
6. Merge: `gh pr merge <N> --merge --delete-branch`
