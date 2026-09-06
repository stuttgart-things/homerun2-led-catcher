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

`duration` does not apply to `text` and `ticker`: a scroll lasts as long as the
scroll takes, which follows from the length of the text. Setting it on those
modes has no effect.

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
