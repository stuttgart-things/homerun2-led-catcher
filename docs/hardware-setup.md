# Hardware Setup

Panel, HAT and library setup. For the full install on a Pi — venv, service unit
and test tooling — see [Raspberry Pi Deployment](raspberry-pi-deployment.md).

## Requirements

- Raspberry Pi 3B+ or newer (Pi 4 recommended)
- 64x64 RGB LED Matrix panel (HUB75 interface)
- Adafruit RGB Matrix HAT or Bonnet (with PWM)
- 5V 4A power supply for the LED matrix
- Raspberry Pi OS Lite (64-bit): Trixie (Python 3.13) or Bookworm (Python 3.11)
- Python 3.11+ (Bullseye's 3.9 is too old for this project)

## Wiring

The Adafruit RGB Matrix HAT connects directly to the Pi's GPIO header. No additional wiring is needed — just attach the HAT and connect the HUB75 ribbon cable from the matrix panel.

## Build rpi-rgb-led-matrix and install

The full walkthrough, from writing the SD card to the systemd unit, is in
[Raspberry Pi Deployment](raspberry-pi-deployment.md). The short version:

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

- The audio driver conflicts with the GPIO/PWM hardware the matrix uses. The line
  needs the word `blacklist`; a bare module name is ignored. Check after the reboot
  with `lsmod | grep snd_bcm2835`, which must print nothing.
- The matrix library compiles against Pillow's C header from Debian's `python3-pil`,
  so the venv must use that same Pillow at runtime, hence `--system-site-packages`.
  Pillow 12 changed the image struct the library reads; a PyPI Pillow in the venv
  would break `image` and `gif`. Details in
  [Raspberry Pi Deployment](raspberry-pi-deployment.md#2-install-the-catcher-and-the-matrix-library).
- There is no `HARDWARE_DESC` to set: every wiring, including `adafruit-hat-pwm`
  for a HAT with the PWM mod, is compiled in and chosen at runtime with
  `LED_HARDWARE_MAPPING`.
- After init the library drops root and runs as `daemon`, which must be able to
  enter your home directory. If `stat -c %a ~` prints `700`, run `chmod 711 ~`.

## Run

```bash
# Panel check without Redis, driven by curl (see testing-with-curl.md)
sudo LED_MODE=standalone LED_API_TOKEN=change-me \
  .venv/bin/python -m led_catcher

# Run with LED matrix only
sudo LED_MODE=led \
  REDIS_ADDR=<redis-host> \
  PROFILE_PATH=$PWD/profile.yaml \
  .venv/bin/python -m led_catcher

# Run with LED matrix + web simulator
sudo LED_MODE=full \
  REDIS_ADDR=<redis-host> \
  PROFILE_PATH=$PWD/profile.yaml \
  .venv/bin/python -m led_catcher
```

> `sudo` is required for GPIO access. The library drops privileges again once the
> panel is initialised (`drop_privileges=True`).

Feed the panel with test messages:

```bash
.venv/bin/led-catcher-publish --demo
```

See [Raspberry Pi Deployment](raspberry-pi-deployment.md) for the systemd unit and
the full test workflow.

## Matrix Configuration

The panel is configured with environment variables:

| Variable | Default |
|---------|---------|
| `LED_ROWS` / `LED_COLS` | `64` |
| `LED_HARDWARE_MAPPING` | `adafruit-hat` (`adafruit-hat-pwm` with the PWM mod) |
| `LED_GPIO_SLOWDOWN` | library default (`2` verified on a Pi 3B+) |
| `LED_BRIGHTNESS` | `100` |
| `LED_PANEL_TYPE`, `LED_PWM_BITS` | unset |

Chain length and parallel are fixed at 1, and privileges are dropped after init
(`drop_privileges=True`). Details and troubleshooting:
[Panel options](raspberry-pi-deployment.md#3b-panel-options).
