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
| `standalone` | LED matrix and web simulator, driven only by the `/display` API — no Redis |

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

> Full walkthrough — Ansible prep, venv, systemd unit, on-device testing:
> [docs/raspberry-pi-deployment.md](docs/raspberry-pi-deployment.md)

### Requirements

- Raspberry Pi (3B+ or newer recommended)
- 64x64 RGB LED Matrix panel
- Adafruit RGB Matrix HAT or Bonnet
- Raspberry Pi OS Lite (64-bit): Trixie (Python 3.13) or Bookworm (Python 3.11)
- Python 3.11+ (Bullseye's 3.9 is too old)

### Install

```bash
# system packages, and the audio driver off (it shares the PWM hardware with the matrix)
sudo apt-get install -y git make g++ cmake python3-dev python3-venv python3-pil
echo "blacklist snd_bcm2835" | sudo tee /etc/modprobe.d/blacklist-rgb-matrix.conf
sudo update-initramfs -u && sudo reboot

# after the reboot: one venv with the matrix library and the catcher
git clone https://github.com/stuttgart-things/homerun2-led-catcher.git ~/homerun2-led-catcher
cd ~/homerun2-led-catcher
python3 -m venv --system-site-packages .venv   # uses Debian's Pillow, see below
git clone --depth 1 https://github.com/hzeller/rpi-rgb-led-matrix.git ~/lib/rpi-rgb-led-matrix
.venv/bin/pip install ~/lib/rpi-rgb-led-matrix
.venv/bin/pip install -e .
```

The matrix library builds its Python bindings with `pip`, straight into the venv.
It compiles against Pillow's C header, which comes from Debian's `python3-pil`. The
venv therefore uses that same Pillow (`--system-site-packages`): a different
Pillow version at runtime makes `image` and `gif` displays read the wrong memory. The `adafruit-hat`/`adafruit-hat-pwm` wiring is
picked at runtime with `LED_HARDWARE_MAPPING`, not at build time.
After init the library drops root and runs as `daemon`: if `stat -c %a ~` prints
`700`, run `chmod 711 ~` so it can still read `fonts/` and `visual_aid/`.

### Run on Pi

```bash
# Panel check without Redis, driven by curl (see docs/testing-with-curl.md)
sudo LED_MODE=standalone LED_API_TOKEN=change-me .venv/bin/python -m led_catcher

# With hardware LED matrix
sudo LED_MODE=led REDIS_ADDR=<redis-host> PROFILE_PATH=$PWD/profile.yaml .venv/bin/python -m led_catcher

# With hardware + web simulator
sudo LED_MODE=full REDIS_ADDR=<redis-host> PROFILE_PATH=$PWD/profile.yaml .venv/bin/python -m led_catcher
```

**As a service, or all of it in one go:** the systemd units (standalone and with
Redis) and an Ansible play that does the whole Pi base install are in
[docs/raspberry-pi-service.md](docs/raspberry-pi-service.md).

> **Note:** `sudo` is required for GPIO access. `rpi-rgb-led-matrix` drops
> privileges again after initialising the panel.

### Send test messages

`led-catcher-publish` writes into the stream exactly like the pitcher does
(`JSON.SET` + `XADD messageID=...`), so the panel can be exercised standalone:

```bash
led-catcher-publish --demo                                    # every display mode
led-catcher-publish --system demo --severity error --title "disk full"
led-catcher-publish --count 50 --interval 0.2                 # load test
led-catcher-publish --dry-run                                 # print payload only
```

Requires a Redis with the RedisJSON module (redis-stack).

## Drive the Panel with curl

`POST /display` puts something on the panel directly, with no message and no profile
rule in between. It works in every mode; `LED_MODE=standalone` needs no Redis at all.
Writes need `LED_API_TOKEN`, and without it the write endpoints don't exist.

```bash
LED_MODE=standalone LED_API_TOKEN=dev LOG_FORMAT=text python -m led_catcher   # or: task run-standalone

curl -X POST http://localhost:8080/display -H 'Authorization: Bearer dev' \
  -H 'Content-Type: application/json' \
  -d '{"kind":"text","text":"DEPLOY LÄUFT","color":[255,165,0]}'

curl http://localhost:8080/display                                                # what is showing
curl -X DELETE http://localhost:8080/display -H 'Authorization: Bearer dev'      # blank it now
```

- Commands for every mode, a walkthrough script and the error responses:
  [docs/testing-with-curl.md](docs/testing-with-curl.md)
- Field reference, auth and limits: [docs/display-api.md](docs/display-api.md)

The web simulator at the same address draws what the panel draws (same fonts, frames and
timing), and has a **Panel control** form that uses the same API.

## Configuration

All configuration is via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `REDIS_ADDR` | `localhost` | Redis host |
| `REDIS_PORT` | `6379` | Redis port |
| `REDIS_PASSWORD` | *(empty)* | Redis password |
| `REDIS_STREAMS` | *(empty)* | Comma-separated streams to consume — takes precedence over `REDIS_STREAM` |
| `REDIS_STREAM` | `messages` | Single stream to consume (legacy; used when `REDIS_STREAMS` is unset) |
| `CONSUMER_GROUP` | `homerun2-led-catcher` | Consumer group name |
| `CONSUMER_NAME` | hostname | Consumer name within group |
| `REDIS_STARTUP_TIMEOUT` | `120s` | How long the consumer retries Redis at startup (Go duration: `90s`, `2m`). When it runs out the process exits non-zero; an invalid value fails startup; SIGTERM ends the wait |
| `LED_MODE` | `full` | Operating mode: `led`, `web`, `full`, `standalone` (no Redis, driven by `/display`) |
| `LED_API_TOKEN` | *(empty)* | Bearer token for `POST`/`DELETE /display`. Empty: the write endpoints are not registered |
| `LED_API_MAX_TEXT` | `256` | Longest `text` accepted by `POST /display` |
| `LED_API_RATE_LIMIT` | `30` | `/display` writes per minute, across all callers |
| `HEALTH_PORT` | `8080` | Health/web server port |
| `PROFILE_PATH` | `profile.yaml` | Path to display rules YAML |
| `UI_STREAM_PRESETS` | *(empty)* | Web simulator one-click presets: comma-separated, `\|`-separated within a preset. Defaults to the configured streams as a single preset |
| `LOG_FORMAT` | `json` | Log format: `json` or `text` |
| `LOG_LEVEL` | `info` | Log level: `debug`, `info`, `warning`, `error` |
| `FONTS_DIR` | `<repo>/fonts` | Directory searched for BDF fonts |
| `VISUAL_AID_DIR` | `<repo>/visual_aid` | Directory searched for images and GIFs |
| `LED_DEFAULT_FONT` | `6x10.bdf` | Fallback font when a rule names a missing one |
| `LED_HARDWARE_MAPPING` | `adafruit-hat` | Panel wiring: `adafruit-hat`, `adafruit-hat-pwm` (PWM mod soldered), `regular`, … |
| `LED_GPIO_SLOWDOWN` | *(library default)* | GPIO slowdown. Raise it if the panel ghosts or glitches. On a Pi 3B+ every value from `0` to `4` is clean; `2` is a safe choice there, not a requirement |
| `LED_BRIGHTNESS` | `100` | Panel brightness, 1–100 |
| `LED_ROWS` | `64` | Panel rows |
| `LED_COLS` | `64` | Panel columns |
| `LED_PANEL_TYPE` | *(empty)* | Panel driver chip needing an init sequence, e.g. `FM6126A` |
| `LED_PWM_BITS` | *(library default)* | PWM bits, 1–11. Lower trades colour depth for refresh rate |
| `LED_IDLE` | `off` | Idle screen: `clock` shows the time whenever nothing else is on the panel, `off` leaves it dark. Switchable at runtime with `PUT /display/idle` |
| `LED_IDLE_COLOR` | `info` | Colour of the idle clock: a profile colour name or `r,g,b` |

## Runtime Stream Switching

The subscribed streams are set from the environment at startup, but can be
switched at runtime on the health/web port — no redeploy, no restart.

This exists for the single-panel case: `REDIS_STREAMS` is additive, so showing
*only* a live scoreboard means subscribing to that stream alone and returning to
`messages` when the match is over.

```bash
# What is currently subscribed
curl -s http://localhost:8080/streams

# Switch to a scoreboard stream only
curl -s -X POST http://localhost:8080/streams \
  -H 'content-type: application/json' \
  -d '{"streams": ["tabletennis"]}'

# Back to normal operation
curl -s -X POST http://localhost:8080/streams \
  -H 'content-type: application/json' \
  -d '{"streams": ["messages"]}'
```

`POST /streams` replaces the whole set and answers with the resulting streams
plus what was added and removed.

**Backlog handling.** A consumer group's last-delivered-id stays put while a
stream is unsubscribed, so re-adding it would replay everything that piled up in
the meantime. Streams being *added* are therefore advanced to `$` before the
switch takes effect. Pass `{"skipBacklog": false}` where the backlog is wanted.
Streams already in the set are left alone, so `["messages"]` →
`["messages", "scale"]` does not disturb `messages`.

### From the web simulator

The same switch is available in the simulator header, so a scorekeeper at the
table does not need a terminal. Buttons come from `UI_STREAM_PRESETS`:

```bash
UI_STREAM_PRESETS=messages,tabletennis        # two buttons
UI_STREAM_PRESETS=messages,tabletennis|scale  # two buttons, the second subscribes to both
```

Presets are comma-separated; the streams inside one preset are `|`-separated.
Unset, the control offers the configured `REDIS_STREAMS` as its single preset.

When the active set differs from the environment configuration the header shows
an `overridden` badge and a `reset` button that switches back — the guard against
the panel silently sitting in scoreboard mode after an abandoned match.

The control posts form-encoded to `POST /ui/streams` and gets the rendered
partial back; the JSON API stays as it is for machine callers. A switch made
through the JSON API or by another client reaches the open UI over SSE, so the
header never shows a stale set.

The control only exists in `LED_MODE=web` and `full`. In `led` mode there is no
web app and the JSON API remains the only path.

**Notes.**

- The switch is picked up on the next `XREADGROUP` iteration, so it lands within
  the 5s block timeout.
- The set is not persisted. A restart returns to the environment configuration —
  a crashed pod comes back in normal operation rather than stuck in scoreboard
  mode.
- The endpoint is unauthenticated, like the existing health and web routes. Fine
  while the port is cluster-internal.

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
    font: 6x10.bdf
    duration: 3

  error-all:
    systems: ["*"]
    severity: [ERROR, CRITICAL]
    kind: text
    text: "{{ system }}: {{ title }}"
    font: 6x10.bdf
    duration: 5

  default-info:
    systems: ["*"]
    severity: [INFO, SUCCESS]
    kind: text
    text: "{{ system }}: {{ title }}"
    font: 6x10.bdf
    duration: 5

colors:
  error: [255, 0, 0]
  critical: [255, 0, 0]
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
├─► Pull Request (Stage 2: build-scan-image.yaml + push-kustomize-pr.yaml)
│     Docker build → push GHCR `pr-<num>-<sha>` → Trivy scan
│     Kustomize OCI → push GHCR `<repo>-kustomize:pr-<num>-<sha>`
│     ArgoCD AppSet spins up a preview env at
│     `led-pr-<num>.homerun2-dev.sthings-vsphere.labul.sva.de`
│     (add the `preview` label to the PR to opt in)
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
| [homerun2-git-pitcher](https://github.com/stuttgart-things/homerun2-git-pitcher) | GitHub watcher — polls GitHub API for events and pitches them to Redis Streams |
| [homerun2-notification-catcher](https://github.com/stuttgart-things/homerun2-notification-catcher) | Notification consumer — routes messages to MS Teams / webhooks via YAML-configured filters |
| [homerun-library](https://github.com/stuttgart-things/homerun-library) | Shared Go library for message types and Redis ops |

## License

Apache-2.0
