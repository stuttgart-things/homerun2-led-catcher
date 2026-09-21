# Raspberry Pi Operations

Testing on the Pi, troubleshooting, and updates. Install:
[Raspberry Pi Deployment](raspberry-pi-deployment.md); the service:
[Run as a service](raspberry-pi-service.md).

## Testing on the Pi

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
| `rgbmatrix not available — running in software-only mode` | the venv has no `rgbmatrix` | `.venv/bin/pip install ~/lib/rpi-rgb-led-matrix`, and run the app with `.venv/bin/python`, not the system `python3` |
| Startup fails, message about `snd_bcm2835` / the sound module | onboard audio still loaded | `blacklist snd_bcm2835` (with the word `blacklist`) in `/etc/modprobe.d/blacklist-rgb-matrix.conf`, `sudo update-initramfs -u`, reboot |
| `image`/`gif` show garbage, or the process crashes on the first image | the venv runs a different Pillow than the header `rgbmatrix` was built against | `PIL.__file__` must be under `/usr/lib/python3/dist-packages`; `.venv/bin/pip uninstall pillow`, so the venv falls back to Debian's |
| Panel works, then `Permission denied` on fonts, images or modules | after init the library runs as `daemon`, which cannot enter a `0700` home | `chmod 711 ~` (see [Prepare the Pi](raspberry-pi-deployment.md#1-prepare-the-pi)) |
| Panel stays dark, no errors | messages match no profile rule | `LOG_LEVEL=debug` shows `no matching display rule`; add a `systems: ["*"]` catch-all |
| Text missing, `font ... not found` warning | font not in any search dir | `task fetch-fonts`, or set `FONTS_DIR` |
| Process exits immediately at startup | `LoadFont()` on a missing path, or no GPIO permission | run with `sudo`, check the font warning above it |
| Heavy flicker | audio driver still loaded, or no PWM mod | `lsmod \| grep snd_bcm2835` must be empty; use `LED_HARDWARE_MAPPING=adafruit-hat` if the bridge is not soldered |
| Ghosting, garbled rows, wrong colours | GPIO too fast for this panel | raise `LED_GPIO_SLOWDOWN` until `test-pattern.png` is clean, or set `LED_PANEL_TYPE=FM6126A` if the panel needs that init sequence. On a Pi 3B+ every value from `0` to `4` was clean, so on that board look at the wiring and the power supply first |
| Process aborts inside `RGBMatrix()` | unknown `hardware_mapping` | the `initializing RGB LED matrix` line above it names the value that was handed over |
| Scoreboard runs off the panel | `score` is laid out for 64x64 only | keep `LED_ROWS`/`LED_COLS` at 64 for that mode — the warning names the size it got |
| `JSON.GET returned None` | stream entry points at a missing document | the producer wrote `XADD` without `JSON.SET` |
| `cannot reach Redis` from the Pi | in-cluster Redis is not exposed | NodePort/Ingress, or an SSH tunnel to the cluster |
| First run shows nothing, later ones work | group created at `id=0`, backlog replays | expected — `XACK` drains it once |
| After a crash or `systemctl kill`, `/healthz` is down for ~15 s | `RestartSec=5`, then ~6 s of Python start-up on a Pi 3B+ before the first log line | expected: measured 13.4 s from kill to a healthy `/healthz` |
| The play reports a different address than the inventory | the Pi is on Ethernet and Wi-Fi at the same time, and the report shows the default-route one | both work; `ip -4 -o addr` lists them. Fix in the play: [stuttgart-things/ansible#1248](https://github.com/stuttgart-things/ansible/issues/1248) |

## Updating

```bash
cd ~/homerun2-led-catcher
git pull                      # or: git fetch --tags && git checkout <new tag>
.venv/bin/pip install -e .
sudo systemctl restart led-catcher
```

The matrix library changes rarely. To update it too:

```bash
git -C ~/lib/rpi-rgb-led-matrix pull
~/homerun2-led-catcher/.venv/bin/pip install ~/lib/rpi-rgb-led-matrix
```
