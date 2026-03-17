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

# Run tests
pytest tests/ -v

# Lint
ruff check src/ tests/

# Format
ruff format src/ tests/

# Run locally
task run
```

## Related Projects

| Project | Description |
|---------|-------------|
| [homerun2-omni-pitcher](https://github.com/stuttgart-things/homerun2-omni-pitcher) | HTTP producer — sends messages to Redis Streams |
| [homerun2-core-catcher](https://github.com/stuttgart-things/homerun2-core-catcher) | Core consumer — log/CLI/web display modes |
| [homerun2-light-catcher](https://github.com/stuttgart-things/homerun2-light-catcher) | WLED light consumer — triggers LED strip effects |
| [homerun-library](https://github.com/stuttgart-things/homerun-library) | Shared Go library for message types and Redis ops |

## License

Apache-2.0
