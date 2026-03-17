# homerun2-led-catcher

RGB LED matrix catcher for the [homerun2](https://github.com/stuttgart-things) ecosystem — consumes messages from Redis Streams and displays them on a 64x64 RGB LED matrix with an embedded HTMX web simulator.

## Architecture

```
Redis Streams → RedisConsumer → log_handler (structured JSON logging)
                              → led_handler (64x64 RGB LED matrix via rpi-rgb-led-matrix)
                              → web_handler (HTMX browser simulator)
```

| Mode | Description |
|------|-------------|
| `led` | Hardware LED matrix only (Raspberry Pi) |
| `web` | HTMX simulator only (browser, no hardware needed) |
| `full` | Both LED matrix and web simulator |

## Quick Start (Development)

```bash
# Clone
git clone https://github.com/stuttgart-things/homerun2-led-catcher.git
cd homerun2-led-catcher

# Install
pip install -e ".[dev]"

# Run in web-only mode (no Raspberry Pi needed)
LED_MODE=web LOG_FORMAT=text REDIS_ADDR=localhost python -m led_catcher
```

The health endpoint is available at `http://localhost:8080/healthz`.

## Raspberry Pi Hardware Setup

### Requirements

- Raspberry Pi (3B+ or newer recommended)
- 64x64 RGB LED Matrix panel
- Adafruit RGB Matrix HAT or Bonnet
- Raspberry Pi OS (Legacy) Lite — Debian Bullseye, 32-bit
- Python 3.11+

### Build rpi-rgb-led-matrix

```bash
git clone https://github.com/hzeller/rpi-rgb-led-matrix.git
cd rpi-rgb-led-matrix

# Configure for Adafruit HAT with PWM
sed -i 's/^HARDWARE_DESC?=regular/#HARDWARE_DESC?=regular/; s/^#HARDWARE_DESC=adafruit-hat-pwm/HARDWARE_DESC=adafruit-hat-pwm/' lib/Makefile

# Build and install Python bindings
cd bindings/python
make build-python && sudo make install-python
```

### Disable audio driver (interferes with LED matrix)

```bash
echo "snd_bcm2835" | sudo tee /etc/modprobe.d/blacklist-rgb-matrix.conf
sudo update-initramfs -u
sudo reboot
```

### Run on Pi

```bash
pip install .

# With hardware LED matrix
sudo LED_MODE=led REDIS_ADDR=<redis-host> PROFILE_PATH=profile.yaml python -m led_catcher

# With hardware + web simulator
sudo LED_MODE=full REDIS_ADDR=<redis-host> PROFILE_PATH=profile.yaml python -m led_catcher
```

> **Note:** `sudo` is required for GPIO access on the Raspberry Pi.

## Configuration

All configuration is via environment variables:

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

## Display Profile

Display rules are defined in a YAML profile that maps (system, severity) to display modes:

```yaml
displayRules:
  github-error:
    systems: [github, gitlab]
    severity: [ERROR]
    kind: gif
    image: sunset.gif
    duration: 5

  scale-weight:
    systems: [scale]
    severity: [INFO]
    kind: static
    text: "{{ message | replace('WEIGHT: ','') }}g"
    font: myfont.bdf
    duration: 3

  default-info:
    systems: ["*"]
    severity: [INFO, SUCCESS]
    kind: text
    text: "{{ system }}: {{ title }}"
    font: myfont.bdf
    duration: 5

colors:
  error: [255, 0, 0]
  warning: [255, 165, 0]
  success: [0, 255, 0]
  info: [0, 100, 255]
```

## Docker

```bash
# Build
docker build -t homerun2-led-catcher:local .

# Run (web mode)
docker run -p 8080:8080 \
  -e LED_MODE=web \
  -e REDIS_ADDR=host.docker.internal \
  homerun2-led-catcher:local
```

## Kubernetes (KCL)

```bash
# Render manifests
kcl run kcl/ -D config.image=ghcr.io/stuttgart-things/homerun2-led-catcher:latest \
             -D config.namespace=homerun2 \
             -D config.redisAddr=redis-stack.homerun2.svc.cluster.local

# Or use a deploy profile
kcl run kcl/ -y tests/kcl-deploy-profile.yaml
```

## Development

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Install pre-commit hook (runs before every git commit)
task setup-precommit
```

### Pre-commit Hook

A git pre-commit hook ensures code quality before every commit. It runs:

1. `ruff check` — linting
2. `ruff format --check` — formatting validation
3. `pytest` — 28 unit tests

Commits are blocked if any check fails. Run manually with `task precommit`.

### Task Commands

```bash
# Local checks (fast, no Docker/Dagger needed)
task precommit         # lint + format-check + test (same as pre-commit hook)
task lint              # ruff check
task format            # ruff format (auto-fix)
task format-check      # ruff format --check
task test              # pytest

# Stage 1: Push — validation via Dagger (same as GH Actions on push)
task ci                # lint + format-check + test + security-scan

# Stage 2: PR — full verification (same as GH Actions on PR)
task ci-pr             # Stage 1 + docker build → ttl.sh → trivy scan

# Stage 3: Release — full release (same as GH Actions release)
task ci-release        # Stage 2 + kustomize OCI push

# Individual Dagger tasks
task ci-lint           # ruff via dagger
task ci-format-check   # ruff format via dagger
task ci-test           # pytest via dagger
task ci-security-scan  # bandit via dagger
task ci-docker-build   # docker build via dagger
task ci-docker-push    # build + push to ttl.sh
task ci-trivy-scan     # trivy scan image on ttl.sh
task ci-push-kustomize # KCL → kustomize OCI to ttl.sh

# Other
task run               # LED_MODE=web, no hardware needed
task build-image       # docker build locally
task pages-local       # build mkdocs locally
```

### CI/CD Pipeline

Every stage runs the same Dagger modules locally (`task`) and in GitHub Actions:

```
Pre-commit (git hook)
│  ruff check + ruff format --check + pytest
│
├─► Push to branch (Stage 1: build-test.yaml)
│     Lint, Format-Check, Test, Security-Scan
│     Local: task ci
│
├─► Pull Request (Stage 2: build-test.yaml)
│     Stage 1 + Docker build → push ttl.sh → Trivy scan
│     Local: task ci-pr
│
├─► Merge to main (Stage 3: release.yaml)
│     Semantic release → Docker push GHCR → KCL kustomize OCI push
│     Local: task ci-release
│
└─► After release (pages.yaml)
      mkdocs-material → GitHub Pages
      Local: task pages-local
```

All CI uses reusable Dagger modules from [`stuttgart-things/dagger`](https://github.com/stuttgart-things/dagger):

| Module | Functions |
|--------|-----------|
| `python` | lint, format-check, test, security-scan, build-image |
| `docker` | build, push |
| `trivy` | scan-image |
| `kcl` | push-kustomize-base |
| `release` | semantic |

## Related Projects

| Project | Description |
|---------|-------------|
| [homerun2-omni-pitcher](https://github.com/stuttgart-things/homerun2-omni-pitcher) | HTTP producer — sends messages to Redis Streams |
| [homerun2-core-catcher](https://github.com/stuttgart-things/homerun2-core-catcher) | Core consumer — log/CLI/web display modes |
| [homerun2-light-catcher](https://github.com/stuttgart-things/homerun2-light-catcher) | WLED light consumer — triggers LED strip effects |
| [homerun-library](https://github.com/stuttgart-things/homerun-library) | Shared Go library for message types and Redis ops |

## License

Apache-2.0
