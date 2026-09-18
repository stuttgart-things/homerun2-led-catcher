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
finite one to run out.

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
  "writable": true
}
```

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
