# Profile Reference

## Overview

Display profiles define how messages are rendered on the LED matrix. Each profile is a YAML file with display rules and color definitions.

## Schema

```yaml
displayRules:
  <rule-name>:
    systems: [<system1>, <system2>, ...]  # or ["*"] for wildcard
    severity: [<severity1>, ...]           # ERROR, CRITICAL, WARNING, INFO, SUCCESS, DEBUG
    kind: <display-mode>                   # static, text, ticker, image, gif
    text: "<jinja2-template>"              # for text modes
    image: "<filename>"                    # for image/gif modes
    font: "<font-file>"                    # BDF font file
    duration: <seconds>                    # display duration
    hold: <true|false>                     # keep it up until replaced

colors:
  error: [255, 0, 0]
  critical: [255, 0, 0]
  warning: [255, 165, 0]
  success: [0, 255, 0]
  info: [0, 100, 255]
  debug: [128, 128, 128]
```

A severity with no entry here renders **white**, which is the colour that means
"unknown severity" — so a severity the bus carries but the map omits is a bug,
not a default.

## Display Modes

| Mode | Description | Required Fields | Honours `hold` |
|------|-------------|-----------------|----------------|
| `static` | Centered text, fixed duration | `text`, `duration` | yes |
| `text` | Scrolling text, left to right | `text` | no |
| `ticker` | Multi-pass scrolling text | `text` | no |
| `image` | Static PNG/JPG, scaled to 64x64 | `image`, `duration` | yes |
| `gif` | Animated GIF playback | `image`, `duration` | no |
| `score` | Table tennis scoreboard | `text` | yes |

`duration` does not apply to `text` and `ticker`: a scroll lasts as long as the
scroll takes, which follows from the length of the text. Setting it on those
modes has no effect.

### `score`

A live table tennis scoreboard, for the running match zaehlwerk pitches onto the
`tabletennis` stream. The other text modes render one line of BDF text, which is
the wrong shape for a score read from across the room mid-rally, so the points
get seven-segment digits at 11x20 and everything around them stays in the 3x5
glyphs the simulator already uses.

```
 y  1- 5   set number left, set score right
 y  7      divider
 y 10-29   points, seven-segment, 11x20 per digit
 y 33-37   player names, serving dot outside the server's name
 y 43-47   status banner
 y 51-55   the last three finished sets
 y 58-62   source of the last point, dimmed
```

`text` carries the whole board as semicolon-separated key/value pairs:

```
points=8:6;sets=1:1;set=3;serve=b;a=ANJA;b=BEN;played=11-8,9-11;banner=SATZBALL ANJA;source=button-a
```

| Key | Meaning | Default |
|-----|---------|---------|
| `points` | points in the set in progress, `a:b` | required |
| `sets` | sets won, `a:b` | `0:0` |
| `set` | number of the set in progress | `1` |
| `serve` | who serves next, `a`/`b` (or `0`/`1`) | not drawn |
| `a`, `b` | player names, truncated at 6 characters | `A`, `B` |
| `played` | finished sets, `11-8,9-11`; the last three are shown | none |
| `banner` | status line — deuce, set point, match point, winner | none |
| `winner` | `a`/`b`; greens the winner and dims the loser | none |
| `source` | device that sent the last point | not drawn |

Unknown keys are ignored, so the pitcher can add fields without a lockstep
release. A payload with no usable `points` falls back to `static` with the raw
text: a panel showing the string beats a panel gone dark, which reads as broken
hardware.

The mode applies no rules of its own. Who serves, whether this is a set point
and who has won all arrive on the wire — zaehlwerk owns the match, the catcher
owns the pixels. The score is also not coloured by severity: a scoreboard that
turned red because the pitcher sent `ERROR` would read as a warning about the
match rather than the match itself.

Use it with `hold: true`. A held score stays up until the next one replaces it,
so the panel is readable between points instead of blanking five seconds after
each one:

```yaml
tabletennis-score:
  systems: [tabletennis]
  severity: [INFO]
  kind: score
  text: "{{ message }}"
  hold: true
```

## `duration` and `hold`

A rule says how long its message owns the panel, and there are two answers.

**A finite `duration`** is a promise of screen time. The message is shown for
that long and is not cut short by a newer one arriving — that one waits its
turn. This is what a notification wants: it has something to say and it should
be readable before the next thing appears.

**`hold: true`** means *until something replaces it*. `duration` is ignored, the
panel is not cleared when the message is over, and the next message takes over
the moment it arrives. This is what a live display wants — a scoreboard, a
weight, a build status. The score of a table tennis match should be readable
between points, not for five seconds after each one.

A held display is therefore pre-emptible by definition, and that is the whole
rule: there is no separate pre-emption setting to combine with this one.

```yaml
displayRules:
  tabletennis-score:
    systems: [tabletennis]
    severity: [INFO, SUCCESS]
    kind: static
    text: "{{ title }}"
    font: 6x10.bdf
    hold: true          # the score stays readable between points
```

### What happens to messages that arrive during a display

The panel is a display, not a queue. One worker thread owns the matrix, with a
single slot for what to show next, and the newest message wins: if three
messages arrive while a finite display is running, the panel shows the third
when it frees up and the first two are dropped. A stale value shown after a
newer one has arrived is worse than not showing it.

Displaying happens on that worker, never on the consumer's event loop, so a
message on the panel never stops the catcher reading its streams or answering
`/healthz`.

## Rule Matching

1. Rules are evaluated in order (first match wins)
2. A rule matches if the message's system is in the rule's `systems` list (or `"*"`)
3. AND the message's severity is in the rule's `severity` list
4. If no rule matches, the message is not displayed on the matrix and is
   logged at `info` level saying so. In the web simulator it is still recorded,
   coloured by its severity rather than rendered by a rule.

Cover every severity your producers actually emit. Until 2026-09-05 the shipped
default had no rule for `ERROR` or `CRITICAL`: on the matrix those were dropped
(logged at `debug`, invisible at the default `LOG_LEVEL`) and in the simulator
they were recorded in the same blue as `INFO`. A failing alert looked exactly
like a working one.

## Jinja2 Templates

Text fields support Jinja2 templating with these variables:

| Variable | Description |
|----------|-------------|
| `{{ title }}` | Message title |
| `{{ message }}` | Message body |
| `{{ severity }}` | Severity level |
| `{{ system }}` | Source system |
| `{{ author }}` | Message author |
| `{{ tags }}` | Tags string |
| `{{ url }}` | Associated URL |

## Example Profile

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

  warning-all:
    systems: ["*"]
    severity: [WARNING]
    kind: text
    text: "{{ system }}: {{ title }}"
    font: 6x10.bdf
    duration: 5

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
