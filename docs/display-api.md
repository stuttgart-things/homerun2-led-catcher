# Display API and standalone mode

> Want to try it now? [Testing with curl](testing-with-curl.md) has
> copy-and-paste commands for every mode.

The panel can be driven directly over HTTP, with no message and no profile
rule in between: `POST /display` says what to show, and the panel shows it.
`curl`, Home Assistant, a cron job or a script on the Pi can all write to the
panel this way, and the web simulator gains a **Panel control** form that uses
the same API.

## Standalone mode

`LED_MODE=standalone` runs the panel and the simulator with **no Redis, no
consumer and no rule matching**. The `/display` API is the only thing that puts
anything on the panel, so the panel, the simulator canvas and the logs cannot
disagree about what is showing.

```bash
LED_MODE=standalone LED_API_TOKEN=change-me LOG_FORMAT=text python -m led_catcher
# or
task run-standalone
```

On the Pi, this is the shortest route from "is the panel OK?" to a lit pixel,
with no redis-stack and no publisher involved:

```bash
sudo LED_MODE=standalone LED_API_TOKEN=change-me LED_GPIO_SLOWDOWN=2 \
     LOG_FORMAT=text .venv/bin/python -m led_catcher
```

In this mode:

- `REDIS_*`, `CONSUMER_*` and `/streams` do not apply. Nothing connects to Redis.
- `PROFILE_PATH` is optional. Only its `colors:` block is used, as the named
  colours a request can ask for.
- `/healthz` reports `"display": "running"`, and answers 503 if the display
  worker thread has died. The process exits non-zero if the worker or the web
  server stops, so a pod restarts visibly instead of staying up with nothing
  working (the lesson of #65).

The `/display` API is also mounted in `led`, `web` and `full` modes. There, a
write goes to the panel just as a matched message would, and it is logged and
recorded in the simulator timeline under the system `api`. In `web` mode there
is no panel, so writes reach only the simulator.

## Auth

The write endpoints (`POST` and `DELETE /display`) need
`Authorization: Bearer $LED_API_TOKEN`.

**If `LED_API_TOKEN` is unset, the write endpoints are not registered at all**,
and a log line says so. This API writes to a physical display in a room, so it
fails closed. `GET /display` and `GET /display/options` are always open: they
reveal nothing that isn't already visible to anyone looking at the panel.

Writes are also limited:

| Variable | Default | |
|---|---|---|
| `LED_API_TOKEN` | *(empty)* | Bearer token. Empty means no write API |
| `LED_API_MAX_TEXT` | `256` | Longest `text` accepted |
| `LED_API_RATE_LIMIT` | `30` | Writes per minute, shared by all callers. Over it: `429` with `Retry-After` |

A request with a wrong token is rejected before it counts against the rate limit.

## `POST /display`

A [`DisplayConfig`](profile-reference.md) over HTTP, without the rule-matching
fields (`systems` and `severity` are rejected).

| Field | Default | |
|---|---|---|
| `kind` | `text` | `static`, `text`, `ticker`, `image`, `gif`, `score` |
| `text` | | Required for `static`, `text`, `ticker`, `score` |
| `image` | | Required for `image` and `gif`. A file name in `visual_aid/`, not a path. An unknown name returns `404` |
| `font` | `6x10.bdf` | A BDF file name in `fonts/`, not a path |
| `color` | `[255,255,255]` | An RGB triple, or a colour name from the profile (`error`, `warning`, `success`, `info`, `debug`, or your own) |
| `duration` | `5` | Seconds, above 0 and at most 600 |
| `hold` | `false` | Stay up until something replaces it. Only the still modes (`static`, `image`, `score`) honour it |

Replacement follows the same rules as a message: a new display replaces a held
one straight away, and waits for a finite one to finish.

```bash
TOKEN=change-me

# scroll a line
curl -sS -X POST localhost:8080/display \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"kind":"text","text":"DEPLOY LÄUFT","color":[255,165,0]}'

# hold a line until something replaces it
curl -sS -X POST localhost:8080/display \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"kind":"static","text":"12:04","hold":true,"color":"info"}'

# play a shipped gif
curl -sS -X POST localhost:8080/display \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"kind":"gif","image":"sunset.gif","duration":10}'
```

## `DELETE /display`

Blanks the panel **immediately**. Unlike a new display, it does not wait for a
finite one to run out. With an [idle screen](#idle-screen-the-clock) set, the
clock comes back; for a dark panel, switch the idle screen off.

```bash
curl -sS -X DELETE localhost:8080/display -H "Authorization: Bearer $TOKEN"
```

## `GET /display`

What is on the panel right now:

```json
{
  "showing": true,
  "held": true,
  "since": "2026-09-18T07:56:09.059991+00:00",
  "display": {"kind": "static", "text": "12:04", "image": "", "font": "6x10.bdf",
              "color": [0, 100, 255], "duration": 5.0, "hold": true},
  "idle": {"mode": "clock", "color": [0, 100, 255], "active": false, "utcOffset": 7200},
  "writable": true
}
```

`idle.active` is true while the idle screen is what the panel shows. `utcOffset`
is the panel's, in seconds east of UTC.

## Idle screen: the clock

With an idle screen, the panel shows the time whenever nothing else is on it,
instead of going dark:

```
        14:05          10x20.bdf, the colon blinks every second
       Monday          6x10.bdf, dimmer than the time
     21.09.2026        6x10.bdf, dimmer than the time
```

The colon and the periods are set as pixels rather than taken from the fonts:
the 10x20 colon sits on the baseline, and the 6x10 period is a 3x3 plus.

- Every display interrupts it at once, from the API or a matched message, and
  is shown exactly as without it.
- It comes back by itself when a finite display ends, and after `DELETE /display`.
- A **held** display stays up. The clock returns only after the held display
  has been replaced by a finite one that has ended.
- The time is the process's local time (the Pi's timezone, or `TZ`), 24h. The
  weekday is spelled out in English; `Wednesday`, the longest, is 54 of 64 px.
- It redraws once a second on the display thread. On a Pi 3B+ that is nothing
  next to the panel's own refresh thread, which keeps one core busy whether the
  panel is lit or not.

At startup, `LED_IDLE=clock` switches it on (default `off`), and `LED_IDLE_COLOR`
sets the colour: a profile colour name or `r,g,b`, default `info`. An unknown
value fails startup.

At runtime:

```bash
curl -sS -X PUT localhost:8080/display/idle \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"mode":"clock","color":"success"}'

curl -sS -X PUT localhost:8080/display/idle \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"mode":"off"}'
```

`color` is optional; without it the clock keeps its colour. The switch is not
persisted: a restart returns to `LED_IDLE`. It counts against the same rate
limit as the other writes. In the simulator, the **Idle screen** row of the
Panel control does the same, and the clock takes the colour from the colour
picker.

## OpenAPI

The API is described in [`docs/openapi.yaml`](https://github.com/stuttgart-things/homerun2-led-catcher/blob/main/docs/openapi.yaml),
generated from the routers (`task openapi`). Backstage shows it as the API
`homerun2-led-catcher-api`. The running service keeps `/openapi.json` switched off,
so nothing extra is exposed on the panel's port.

## `GET /display/options`

The kinds, fonts, images and named colours a request can use, plus the limits.
The simulator's control form is populated from this.

## Panel control in the simulator

When the write API is enabled, the simulator page shows a **Panel control**
form under the canvas. It is a plain client of the endpoints above. The token
is typed in, kept in the tab's `sessionStorage`, and sent only as the
`Authorization` header. It is never embedded in the page.

## The simulator canvas draws what the panel draws

The canvas renders every display the way the matrix does (#83), from the
panel's own inputs:

- **Text** uses the same BDF fonts. `GET /api/preview/text?font=…&text=…`
  returns the glyph bitmaps and font box, so widths, centring and scroll length
  match the panel to the pixel. `static` is centred, `text` scrolls 1px per 30 ms,
  and `ticker` scrolls three times.
- **Images and GIFs** use the frames the panel plays. `GET /api/preview/image/{name}`
  returns the frames from the same `load_animation`, so scaling, letterboxing
  and frame timing are the panel's own.
- **Replacement** follows the display worker: a held display gives way at once,
  a finite one keeps its time, and a blank is immediate.
- A message that no rule matched is listed in the timeline but not drawn,
  because the panel does not show it either.
- When nothing is displayed, the canvas stays dark, as the panel does.

This makes the canvas a reference for hardware checks: it shows what the panel
*should* show, side by side with what it does show.
