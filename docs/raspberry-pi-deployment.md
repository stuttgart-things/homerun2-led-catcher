# Raspberry Pi Deployment

End-to-end guide for running `homerun2-led-catcher` **natively on a Raspberry Pi**
driving a physical 64x64 HUB75 matrix. This is the deployment path that replaces
the `homerun-matrix-catcher` tarball install.

For the panel wiring and the PWM modification, see [Hardware Setup](hardware-setup.md).
For Kubernetes, see [Deployment](deployment.md).

## Why native and not a container

The release image is published for `linux/amd64` **and** `linux/arm64`, so it does
run on a 64-bit Pi — but driving the matrix from inside a container means
`--privileged` (or at least `/dev/mem` plus `SYS_RAWIO`), running as root against
the image's `USER 65532`, and installing the `rgbmatrix` bindings into the image
yourself. For a single appliance-style Pi the native install is simpler to operate
and to debug. Use the container image on the Pi only if you already run everything
there under Docker.

## Prerequisites

| Component | Value |
|-----------|-------|
| Board | Raspberry Pi 3B+ or newer (Pi 4 recommended) |
| Panel | 64x64 RGB LED matrix, HUB75 |
| HAT | Adafruit RGB Matrix HAT or Bonnet |
| Power | 5V 4A for the panel (separate from the Pi supply) |
| OS | **Raspberry Pi OS Bookworm Lite (64-bit)** |
| Python | 3.11+ |
| Redis | reachable redis-stack instance (RedisJSON required) |

!!! warning "Bookworm, not Bullseye"
    Earlier docs recommended Raspberry Pi OS Legacy (Bullseye), which ships
    Python 3.9. This project requires **Python 3.11+** (`pyproject.toml`), so
    Bullseye cannot run it without building Python from source. Bookworm ships
    3.11 and is the supported baseline.

## 1. Prepare the Pi

Ansible play for a fresh Bookworm image:

```yaml
# /tmp/raspi-betankung.yaml
- name: Prepare Raspberry Pi for homerun2-led-catcher
  hosts: "{{ target_host | default('all') }}"
  become: yes
  vars:
    os_packages:
      - git
      - curl
      - unzip
      - wget
      - make
      - g++
      - python3-pip
      - python3-dev
      - python3-venv
      - cython3

  tasks:
    - name: Update apt cache
      ansible.builtin.apt:
        update_cache: yes

    - name: Install build and runtime packages
      ansible.builtin.apt:
        name: "{{ os_packages }}"
        state: present

    - name: Blacklist the onboard audio driver
      ansible.builtin.copy:
        content: "snd_bcm2835\n"
        dest: /etc/modprobe.d/blacklist-rgb-matrix.conf
        mode: "0644"
      notify: update initramfs

  handlers:
    - name: update initramfs
      ansible.builtin.command: update-initramfs -u
```

```bash
ansible-playbook -i /tmp/inventory_raspi /tmp/raspi-betankung.yaml -vv
```

`python3-venv` is new compared to the old play: Bookworm marks the system Python
as externally managed (PEP 668), so `pip install` into it is refused. Everything
below uses a venv.

The audio driver shares the GPIO/PWM hardware with the matrix — leaving it loaded
causes flicker. **Reboot after this play** so the blacklist takes effect.

## 2. Build the matrix library

```bash
mkdir -p ~/lib && cd ~/lib
git clone https://github.com/hzeller/rpi-rgb-led-matrix.git
cd rpi-rgb-led-matrix

# Adafruit HAT with the PWM mod — better refresh, far less flicker.
# Skip this if you did NOT solder the GPIO4↔GPIO18 bridge.
sed -i 's/^HARDWARE_DESC?=regular/#HARDWARE_DESC?=regular/; s/^#HARDWARE_DESC=adafruit-hat-pwm/HARDWARE_DESC=adafruit-hat-pwm/' lib/Makefile

cd bindings/python
make build-python
sudo make install-python
```

`make install-python` installs `rgbmatrix` into the **system** Python. The venv in
the next step is created with `--system-site-packages` so it can see it.

Verify:

```bash
python3 -c "import rgbmatrix; print('rgbmatrix ok')"
```

## 3. Install the catcher

```bash
cd ~
git clone https://github.com/stuttgart-things/homerun2-led-catcher.git
cd homerun2-led-catcher

python3 -m venv --system-site-packages .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e .
```

Two details that matter:

- **`--system-site-packages`** — without it the venv cannot see `rgbmatrix` and the
  app silently starts in software-only mode (log line
  `rgbmatrix not available — running in software-only mode`).
- **`-e` (editable)** — keeps `fonts/` and `visual_aid/` resolvable next to the
  code. For a non-editable install, set `FONTS_DIR` and `VISUAL_AID_DIR` explicitly.

Confirm the hardware path is active:

```bash
.venv/bin/python -c "from led_catcher.display.matrix import HAS_RGBMATRIX; print(HAS_RGBMATRIX)"
# True
```

## 4. Fonts and images

The repo ships three public-domain BDF fonts in `fonts/` (`4x6`, `6x10`, `7x13`),
so text rendering works out of the box. Only BDF is supported — `LoadFont()` cannot
read TTF/OTF.

```bash
task fetch-fonts          # optional: pull the full upstream font set
cp my-animation.gif visual_aid/   # images/GIFs for kind: image / kind: gif
```

Both directories are overridable, which is what a non-editable or packaged install
needs:

| Variable | Default | Description |
|----------|---------|-------------|
| `FONTS_DIR` | `<repo>/fonts` | directory searched for BDF fonts |
| `VISUAL_AID_DIR` | `<repo>/visual_aid` | directory searched for images and GIFs |
| `LED_DEFAULT_FONT` | `6x10.bdf` | font used when a rule names a missing font |

Lookup order is `$FONTS_DIR` → repo root → package dir → `/app/fonts` → the name as
an absolute path. A rule referencing a missing font logs a warning and falls back
to `LED_DEFAULT_FONT` instead of killing the process.

## 5. Profile

```bash
cp tests/profile.yaml profile.yaml
```

Adjust rules to the systems you actually publish from — see
[Profile Reference](profile-reference.md). Keep `duration` short (3–5s) on a single
panel, otherwise a burst of messages queues up behind the current one.

## 6. First manual run

```bash
sudo LED_MODE=led \
     REDIS_ADDR=<redis-host> \
     REDIS_PORT=6379 \
     REDIS_STREAM=messages \
     CONSUMER_GROUP=homerun2-led-catcher \
     PROFILE_PATH=$PWD/profile.yaml \
     LOG_FORMAT=text LOG_LEVEL=debug \
     .venv/bin/python -m led_catcher
```

Expected startup lines:

```
RGB LED matrix initialized (64x64, adafruit-hat)
LED handler active
consumer starting
```

`sudo` is required for GPIO. The library drops privileges again right after
initialising the panel (`drop_privileges=True` in `display/matrix.py`), so the
process does not stay root.

Use `LED_MODE=full` to additionally serve the HTMX simulator on `HEALTH_PORT` —
handy for watching what the panel *should* be showing from a laptop.

## 7. Run as a service

```bash
sudo tee /etc/systemd/system/led-catcher.service > /dev/null <<'EOF'
[Unit]
Description=homerun2-led-catcher
Documentation=https://github.com/stuttgart-things/homerun2-led-catcher
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
# Root is required for GPIO; rpi-rgb-led-matrix drops privileges after init.
User=root
WorkingDirectory=/home/sthings/homerun2-led-catcher
Environment=LED_MODE=full
Environment=REDIS_ADDR=redis.example.com
Environment=REDIS_PORT=6379
Environment=REDIS_STREAM=messages
Environment=CONSUMER_GROUP=homerun2-led-catcher
Environment=PROFILE_PATH=/home/sthings/homerun2-led-catcher/profile.yaml
Environment=HEALTH_PORT=8080
Environment=LOG_FORMAT=json
Environment=LOG_LEVEL=info
# Redis password, if any — keep it out of the unit file:
# EnvironmentFile=-/etc/default/led-catcher
ExecStart=/home/sthings/homerun2-led-catcher/.venv/bin/python -m led_catcher
Restart=always
RestartSec=5
KillSignal=SIGTERM
TimeoutStopSec=20

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now led-catcher
journalctl -u led-catcher -f
```

`CONSUMER_NAME` defaults to the hostname, so several Pis on the same stream share
the consumer group and split messages between them. Set it explicitly if you run
more than one instance per host.

Put the Redis password in `/etc/default/led-catcher` (mode `0600`), not in the unit:

```bash
printf 'REDIS_PASSWORD=%s\n' 'my-secret' | sudo tee /etc/default/led-catcher > /dev/null
sudo chmod 600 /etc/default/led-catcher
```

## 8. Testing on the Pi

The package ships a publisher so the panel can be exercised without the rest of the
platform. It writes exactly what the consumer expects: a RedisJSON document plus a
stream entry pointing at it.

```bash
# Walk through every display mode in the shipped profile
.venv/bin/led-catcher-publish --demo

# One specific message
.venv/bin/led-catcher-publish --system demo --severity error --title "disk full" \
                              --message "node-01 at 98%"

# Load test — 50 messages, 200ms apart
.venv/bin/led-catcher-publish --count 50 --interval 0.2

# See the payload without writing anything
.venv/bin/led-catcher-publish --dry-run
```

It reads `REDIS_ADDR` / `REDIS_PORT` / `REDIS_PASSWORD` / `REDIS_STREAM` from the
environment, same as the catcher, so exporting them once covers both:

```bash
export REDIS_ADDR=redis.example.com REDIS_STREAM=messages
```

### Without the tool

The same two commands by hand, useful when debugging against `redis-cli`:

```bash
redis-cli -h "$REDIS_ADDR" JSON.SET led-test:1 '$' \
  '{"title":"manual","message":"hello","severity":"info","system":"demo","author":"me"}'
redis-cli -h "$REDIS_ADDR" XADD messages '*' messageID led-test:1
```

### A local Redis on the Pi

To test with no cluster involved at all:

```bash
docker run -d --name redis-stack -p 6379:6379 redis/redis-stack-server:latest
export REDIS_ADDR=localhost
```

RedisJSON is mandatory — plain `redis:7` will not work, because every stream entry
is resolved through `JSON.GET`.

### Inspecting stream state

```bash
redis-cli XLEN messages                                  # entries in the stream
redis-cli XINFO GROUPS messages                          # lag and pending per group
redis-cli XPENDING messages homerun2-led-catcher         # unacked entries
curl -s http://<pi>:8080/healthz                         # liveness
```

A growing `pending` count means handlers are slower than the inflow — usually a
`duration` set too high in the profile.

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `rgbmatrix not available — running in software-only mode` | venv cannot see the bindings | recreate the venv with `--system-site-packages`, or rerun `sudo make install-python` |
| Panel stays dark, no errors | messages match no profile rule | `LOG_LEVEL=debug` shows `no matching display rule`; add a `systems: ["*"]` catch-all |
| Text missing, `font ... not found` warning | font not in any search dir | `task fetch-fonts`, or set `FONTS_DIR` |
| Process exits immediately at startup | `LoadFont()` on a missing path, or no GPIO permission | run with `sudo`, check the font warning above it |
| Heavy flicker | audio driver still loaded, or no PWM mod | verify the blacklist and that you rebooted; rebuild without `adafruit-hat-pwm` if the bridge is not soldered |
| `JSON.GET returned None` | stream entry points at a missing document | the producer wrote `XADD` without `JSON.SET` |
| `cannot reach Redis` from the Pi | in-cluster Redis is not exposed | NodePort/Ingress, or an SSH tunnel to the cluster |
| First run shows nothing, later ones work | group created at `id=0`, backlog replays | expected — `XACK` drains it once |

## Updating

```bash
cd ~/homerun2-led-catcher
git pull
.venv/bin/pip install -e .
sudo systemctl restart led-catcher
```
