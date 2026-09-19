# Testing the panel with curl

The quickest way to check a panel, a font, an image or a GIF is to put it on the
matrix yourself with `curl`, with no Redis, no publisher and no profile rule in
between. This page is the practical version: start the service, then copy the
commands. The full field reference is in [Display API](display-api.md).

What you send is what the panel shows, and the [web simulator](#watch-it-in-the-simulator)
at the same address shows the same thing pixel for pixel. Keep it open next to
the panel.

## 1. Start it

The write endpoints only exist when `LED_API_TOKEN` is set. Pick any string.

**Laptop, no hardware:**

```bash
task run-standalone          # LED_MODE=standalone, token "dev", port 8080
```

**Raspberry Pi:**

```bash
cd ~/homerun2-led-catcher
sudo LED_MODE=standalone LED_API_TOKEN=change-me LED_GPIO_SLOWDOWN=2 \
     LOG_FORMAT=text .venv/bin/python -m led_catcher
```

Already running as the systemd service? Put `LED_API_TOKEN=change-me` into
`/etc/default/led-catcher` and restart it; see
[Run as a service](raspberry-pi-deployment.md#7-run-as-a-service).

**Kubernetes:** set `apiToken` in the KCL config (it becomes the `<name>-api`
Secret), then:

```bash
kubectl -n homerun2 port-forward svc/homerun2-led-catcher 8080:80
```

`LED_MODE=standalone` needs no Redis at all. In `led`, `web` and `full` the API
works alongside the Redis consumer, and a request replaces or waits for a
message's display by the same rules.

## 2. Set up your shell

```bash
export LED=http://localhost:8080      # or http://<pi-address>:8080
export TOKEN=dev                      # whatever LED_API_TOKEN is

# a small helper, so the examples below stay one line each
panel() {
  curl -sS -X POST "$LED/display" \
    -H "Authorization: Bearer $TOKEN" \
    -H 'Content-Type: application/json' \
    -d "$1"
  echo
}
```

Check it is up and writable:

```bash
curl -sS $LED/healthz; echo           # {"status":"ok", ..., "display":"running"}
curl -sS $LED/display/options; echo   # kinds, fonts, images, colours, limits, "writable":true
```

`"writable": false` means `LED_API_TOKEN` is not set, and every write returns `405`.

## 3. Put things on the panel

Each call answers `{"accepted":true,"panel":true,"display":{…}}`.
`"panel": false` means nothing drives hardware here (`LED_MODE=web`), so the
display only reached the simulator.

### Text

```bash
# scroll one line right to left, then go dark
panel '{"kind":"text","text":"DEPLOY LÄUFT","color":[255,165,0]}'

# the same, three times
panel '{"kind":"ticker","text":"BUILD GREEN","color":"success"}'

# centred and still, for 8 seconds
panel '{"kind":"static","text":"12:04","font":"7x13.bdf","duration":8}'

# centred and still until something replaces it
panel '{"kind":"static","text":"12:04","font":"7x13.bdf","hold":true}'
```

Fonts shipped: `4x6.bdf`, `6x10.bdf` (default), `7x13.bdf`. `static` text that
is wider than the panel is clipped, not scrolled, and the service logs how many
pixels were cut. Use `text` or `ticker` for long lines.

### Colours

`color` takes an RGB triple or a colour name from the profile:

```bash
panel '{"kind":"static","text":"RGB","color":[0,200,255],"duration":5}'
panel '{"kind":"static","text":"ERROR","color":"error","duration":5}'     # also: warning, success, info, debug
```

Without a colour the text is white.

### Images and GIFs

Names are files in `visual_aid/`. `curl -sS $LED/display/options` lists them.

```bash
# the panel test pattern: border, corner colours, ramps, checkerboard, centre cross
panel '{"kind":"image","image":"test-pattern.png","hold":true}'

# a GIF for 10 seconds (sunset.gif loops every 1.92 s, so about 5 loops)
panel '{"kind":"gif","image":"sunset.gif","duration":10}'
```

An image is scaled to the panel with its aspect ratio kept, and the unused rows
or columns stay dark. To try your own, copy it into `visual_aid/` (or
`$VISUAL_AID_DIR`). No restart is needed.

### Scoreboard

```bash
panel '{"kind":"score","text":"points=7:5;sets=1:0;set=2;serve=a;a=Anna;b=Ben","hold":true}'
panel '{"kind":"score","text":"points=11:9;sets=2:0;set=2;winner=a;banner=Match;a=Anna;b=Ben","hold":true}'
```

Keys: `points`, `sets`, `set`, `serve` (`a`/`b`), `winner`, `a`, `b` (names,
6 characters max), `played` (e.g. `11-7,9-11`), `banner`, `source`. A text
without `points` falls back to plain static text.

## 4. Replace, hold and blank

The panel shows one thing at a time, and the newest request wins, with these rules:

| On the panel now | New `POST` | What happens |
|---|---|---|
| nothing | anything | shown at once |
| a held display (`"hold":true`) | anything | replaces it at once |
| a finite display | anything | waits until the current one has had its full `duration` |
| a finite display | several in a row | only the newest is shown next; the others are dropped |

You can see this with two commands:

```bash
panel '{"kind":"static","text":"FIRST","color":"error","duration":5}'
panel '{"kind":"static","text":"SECOND","color":"success","hold":true}'
# FIRST stays for its 5 seconds, then SECOND appears and stays
```

`hold` works for the still kinds: `static`, `image` and `score`. `text`,
`ticker` and `gif` run for as long as their animation takes.

Ask what is on the panel (no token needed):

```bash
curl -sS $LED/display; echo
# {"showing":true,"held":true,"since":"…","display":{"kind":"static","text":"SECOND",…},"writable":true}
```

Make it dark, **immediately**, even in the middle of a finite display:

```bash
curl -sS -X DELETE $LED/display -H "Authorization: Bearer $TOKEN"; echo
```

## 5. Walk through every mode

Copy and paste this. It shows each kind in turn and ends with a dark panel, in
about 45 seconds. Watch the panel and the simulator side by side.

```bash
step() { echo "── $1"; panel "$2" >/dev/null; sleep "$3"; }

step "test pattern (check border, corners: red TL, green TR, blue BL, white BR)" \
     '{"kind":"image","image":"test-pattern.png","hold":true}' 8
step "static 4x6"   '{"kind":"static","text":"4x6 font","font":"4x6.bdf","hold":true}' 3
step "static 6x10"  '{"kind":"static","text":"6x10","font":"6x10.bdf","hold":true}' 3
step "static 7x13"  '{"kind":"static","text":"12:04","font":"7x13.bdf","color":"info","hold":true}' 3
step "scroll"       '{"kind":"text","text":"SCROLL ME","color":[255,165,0]}' 4
step "ticker (3x)"  '{"kind":"ticker","text":"TICKER","color":"success"}' 10
step "gif, 6 s"     '{"kind":"gif","image":"sunset.gif","duration":6}' 7
step "scoreboard"   '{"kind":"score","text":"points=7:5;sets=1:0;set=2;serve=a;a=Anna;b=Ben","hold":true}' 5
echo "── blank"; curl -sS -X DELETE $LED/display -H "Authorization: Bearer $TOKEN"; echo
```

That is 9 writes, well inside the default limit of 30 a minute.

## 6. When it says no

| Status | Meaning | Fix |
|---|---|---|
| `405 Method Not Allowed` | the write endpoints are not registered | set `LED_API_TOKEN` and restart |
| `401` `missing or wrong bearer token` | token missing or different | check `$TOKEN` and the `Authorization: Bearer …` header |
| `400` `kind 'text' needs a text` | the kind is missing what it draws | add `text` (text kinds) or `image` (`image`, `gif`) |
| `400` `text is longer than 256 characters` | over `LED_API_MAX_TEXT` | shorten it, or raise the limit |
| `400` `unknown colour 'mauve'` | not an RGB triple or a profile colour name | the error lists the valid names |
| `404` `image 'x.gif' not found` | no such file in `visual_aid/` | check `GET /display/options` |
| `422` | the body is malformed: unknown kind, `duration` ≤ 0 or > 600, RGB out of 0–255, a path instead of a file name, or `systems`/`severity` (not accepted here) | the response's `detail` names the field |
| `429` + `Retry-After` | over `LED_API_RATE_LIMIT` writes per minute, counted across all callers | wait the given seconds, or raise the limit for a test session |

A request with a wrong token does not count against the rate limit.

## Watch it in the simulator

Open `$LED/` in a browser (`LED_MODE` `standalone`, `web` or `full`). The canvas
draws what the panel draws: the same fonts, the same image frames and the same
timing. The timeline on the right lists every request under the system `api`. So
the panel and the canvas can be compared directly, and a difference between them
is a finding.

When the write API is enabled the page also has a **Panel control** form. It
sends the same requests as the commands above: enter the token, pick a kind, and
press Show or Blank.
