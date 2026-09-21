# Run as a service

The Pi runs the catcher as a systemd service, and the
[Ansible play](#ansible-the-whole-base-install-in-one-run) can set up everything,
from a freshly written SD card to the running service. The manual install is in
[Raspberry Pi Deployment](raspberry-pi-deployment.md); tests, troubleshooting and
updates are in [Raspberry Pi Operations](raspberry-pi-operations.md).

There are two units below: **standalone**, for the hardware tests and a panel
driven only by the `/display` API, and **full**, with Redis. The Ansible play
writes either one.

Secrets go into `/etc/default/led-catcher` (mode `0600`), not into the unit,
which anyone on the Pi can read:

```bash
printf 'LED_API_TOKEN=%s\n' 'change-me' | sudo tee /etc/default/led-catcher > /dev/null
sudo chmod 600 /etc/default/led-catcher
```

Pick your own token instead of `change-me`. With Redis, add `REDIS_PASSWORD=…`
to the same file.

## Standalone (no Redis)

```bash
sudo tee /etc/systemd/system/led-catcher.service > /dev/null <<'EOF'
[Unit]
Description=homerun2-led-catcher
Documentation=https://github.com/stuttgart-things/homerun2-led-catcher
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
# root is needed for GPIO; the matrix library drops to "daemon" after init
User=root
WorkingDirectory=/home/sthings/homerun2-led-catcher
Environment=LED_MODE=standalone
Environment=LED_HARDWARE_MAPPING=adafruit-hat
Environment=LED_GPIO_SLOWDOWN=2
Environment=LED_BRIGHTNESS=100
# the time on the panel whenever nothing else is up; off for a dark panel
Environment=LED_IDLE=clock
Environment=HEALTH_PORT=8080
Environment=LOG_FORMAT=text
Environment=LOG_LEVEL=info
# LED_API_TOKEN (and REDIS_PASSWORD with Redis)
EnvironmentFile=/etc/default/led-catcher
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
```

Replace `sthings` with your user. Use `LED_HARDWARE_MAPPING=adafruit-hat-pwm` if
the PWM bridge is soldered.

## Full (with Redis)

The same unit with `LED_MODE=full` and the Redis settings:

```ini
Environment=LED_MODE=full
Environment=REDIS_ADDR=redis.example.com
Environment=REDIS_PORT=6379
Environment=REDIS_STREAM=messages
Environment=CONSUMER_GROUP=homerun2-led-catcher
Environment=PROFILE_PATH=/home/sthings/homerun2-led-catcher/profile.yaml
```

The `/display` API keeps working in `full` mode as long as `LED_API_TOKEN` is set.

`CONSUMER_NAME` defaults to the hostname, so several Pis on the same stream share
the consumer group and split messages between them. Set it explicitly if you run
more than one instance per host.

## Check it

```bash
systemctl status led-catcher
journalctl -u led-catcher -f        # Ctrl+C to stop following
curl -s localhost:8080/healthz; echo
```

The log shows `RGB LED matrix initialized (…)`, and `/healthz` reports
`"display":"running"`.

## Day to day

| What | Command |
|---|---|
| Restart, e.g. after an update | `sudo systemctl restart led-catcher` |
| Stop | `sudo systemctl stop led-catcher` |
| Don't start at boot any more | `sudo systemctl disable led-catcher` |
| After editing the unit | `sudo systemctl daemon-reload && sudo systemctl restart led-catcher` |
| Log since the last boot | `journalctl -u led-catcher -b` |

`Restart=always` brings the service back after a crash, but not instantly. On a
Pi 3B+ `/healthz` answers about 15 s after the process died: 5 s `RestartSec`,
then about 6 s of Python start-up before the first log line, and a moment for the
panel and the web server.

!!! warning "One process per panel"
    While the service runs, it owns the panel and port 8080. Stop it
    (`sudo systemctl stop led-catcher`) before starting `python -m led_catcher` by
    hand, or two processes fight over the matrix.

## Ansible: the whole base install in one run

The play `sthings.container.homerun2_led_catcher_pi`, in the
[`sthings.container` collection](https://github.com/stuttgart-things/ansible/tree/main/collections/container)
of `stuttgart-things/ansible`, does the whole [install](raspberry-pi-deployment.md) and
this page on a freshly written SD card:

- the boot config, the audio blacklist and a reboot (only when those changed)
- the packages, `python3-pil` included
- the home permissions
- the catcher and the matrix library in the venv, with the Pillow check
- the secrets file and the unit, with `VERSION` and `COMMIT` for `/healthz`, then a `/healthz` check

Running it again changes nothing; the matrix library is only rebuilt when its
checkout moves. On a first install, the service starts exactly once.

```ini
# inventory.ini
[ledpi]
192.168.10.120 ansible_user=sthings
```

```bash
COLLECTION_VERSION=26.921.1350   # a sthings-container release
ansible-galaxy collection install -f \
  https://github.com/stuttgart-things/ansible/releases/download/sthings-container-${COLLECTION_VERSION}/sthings-container-${COLLECTION_VERSION}.tar.gz

# standalone, as for the hardware tests
ansible-playbook -i inventory.ini sthings.container.homerun2_led_catcher_pi -e led_api_token=change-me

# with Redis
ansible-playbook -i inventory.ini sthings.container.homerun2_led_catcher_pi \
  -e led_mode=full -e led_redis_addr=redis.example.com \
  -e led_api_token=change-me -e led_redis_password=secret
```

The play's header lists every variable: `led_catcher_version`,
`led_hardware_mapping`, `led_gpio_slowdown`, `led_allow_reboot`, and more.

### Without a local Ansible: through Dagger

The [`ansible` module](https://github.com/stuttgart-things/dagger/tree/main/ansible) of
`stuttgart-things/dagger` runs the play in a container: only Dagger and Docker are
needed on the machine. This is how the hardware tests were run
([#96](https://github.com/stuttgart-things/homerun2-led-catcher/issues/96)). It logs
in with a password, over `sshpass`, and does not check the Pi's host key.

```yaml
# requirements.yml: the collection, from its release
collections:
  - name: https://github.com/stuttgart-things/ansible/releases/download/sthings-container-26.921.1350/sthings-container-26.921.1350.tar.gz
    type: url
```

```bash
# the inventory without ansible_user: the module passes the user
printf '[ledpi]\n192.168.10.120\n' > inventory.ini

# secrets only in the environment, never on the command line
export SSH_USER=sthings SSH_PASSWORD='…'
printf 'LED_API_TOKEN=%s\n' 'change-me' > ansible.env   # chmod 600, delete afterwards

dagger -m github.com/stuttgart-things/dagger/ansible call execute \
  --src . \
  --requirements requirements.yml \
  --inventory inventory.ini \
  --playbooks sthings.container.homerun2_led_catcher_pi \
  --ssh-user env:SSH_USER \
  --ssh-password env:SSH_PASSWORD \
  --env-secrets file:ansible.env \
  --parameters "ansible_become_password='{{ lookup(\"env\", \"ANSIBLE_PASSWORD\") }}' led_api_token='{{ lookup(\"env\", \"LED_API_TOKEN\") }}' run_id=$(date +%s%N)" \
  --progress plain
```

- **`run_id=$(date +%s%N)` is not optional.** Dagger caches the `ansible-playbook`
  step. Without a value that changes, a second identical call replays the first
  result and never touches the Pi, which looks exactly like a run that changed
  nothing.
- The extra-vars carry only `lookup("env", …)` expressions: the password and the
  token reach Ansible as Dagger secrets and stay masked in the `-vv` output.
  `ANSIBLE_PASSWORD` is set by the module from `--ssh-password`, and is reused
  here as the `sudo` password.
- Further variables go into `--parameters` the same way, e.g.
  `led_catcher_version=v0.12.0`.

