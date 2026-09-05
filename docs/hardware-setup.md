# Hardware Setup

Panel, HAT and library setup. For the full install on a Pi — venv, service unit
and test tooling — see [Raspberry Pi Deployment](raspberry-pi-deployment.md).

## Requirements

- Raspberry Pi 3B+ or newer (Pi 4 recommended)
- 64x64 RGB LED Matrix panel (HUB75 interface)
- Adafruit RGB Matrix HAT or Bonnet (with PWM)
- 5V 4A power supply for the LED matrix
- Raspberry Pi OS Bookworm Lite (64-bit) — ships Python 3.11
- Python 3.11+ (Bullseye's 3.9 is too old for this project)

## Wiring

The Adafruit RGB Matrix HAT connects directly to the Pi's GPIO header. No additional wiring is needed — just attach the HAT and connect the HUB75 ribbon cable from the matrix panel.

## Build rpi-rgb-led-matrix

```bash
# Clone the library
git clone https://github.com/hzeller/rpi-rgb-led-matrix.git
cd rpi-rgb-led-matrix

# Configure for Adafruit HAT with PWM (better quality, less flicker)
sed -i 's/^HARDWARE_DESC?=regular/#HARDWARE_DESC?=regular/' lib/Makefile
sed -i 's/^#HARDWARE_DESC=adafruit-hat-pwm/HARDWARE_DESC=adafruit-hat-pwm/' lib/Makefile

# Build and install Python bindings
cd bindings/python
make build-python
sudo make install-python
```

## Disable Audio Driver

The audio driver conflicts with the GPIO pins used by the LED matrix. Disable it:

```bash
echo "snd_bcm2835" | sudo tee /etc/modprobe.d/blacklist-rgb-matrix.conf
sudo update-initramfs -u
sudo reboot
```

## Install and Run

Bookworm marks the system Python as externally managed, and the `rgbmatrix`
bindings land in the system site-packages — so use a venv that can see them:

```bash
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -e .

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

The display is configured for a single 64x64 panel with Adafruit HAT:

| Setting | Value |
|---------|-------|
| Rows | 64 |
| Columns | 64 |
| Chain length | 1 |
| Parallel | 1 |
| Hardware mapping | adafruit-hat |
| Drop privileges | True |

To modify these values, edit `src/led_catcher/display/matrix.py`.
