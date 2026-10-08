# Profile Reference

## Overview

Display profiles define how messages are rendered on the LED matrix. Each profile is a YAML file with display rules and color definitions.

## Schema

```yaml
displayRules:
  <rule-name>:
    systems: [<system1>, <system2>, ...]  # or ["*"] for wildcard
    severity: [<severity1>, ...]           # ERROR, CRITICAL, WARNING, INFO, SUCCESS, DEBUG
    tags: [<tag1>, ...]                    # optional; every one must be a whole element of the message's tags
    kind: <display-mode>                   # static, text, ticker, image, gif, score, card
    text: "<jinja2-template>"              # for text modes; card: the scrolling line
    image: "<filename>"                    # for image/gif modes
    font: "<font-file>"                    # BDF font file (not used by score, card)
    duration: <seconds>                    # display duration
    hold: <true|false>                     # keep it up until replaced
    strip_prefix: <true|false>             # card: drop "[SEVERITY] system" from the title

quietHours:                              # optional, see "Quiet hours"
  from: "19:00"
  to: "07:00"
  weekends: true
  timezone: Europe/Berlin
  allow: [error, critical]

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
| `card` | The whole event: system, title, message, author, time | none | yes |

`duration` does not apply to `text` and `ticker`: a scroll lasts as long as the
scroll takes, which follows from the length of the text. Setting it on those
modes has no effect. It does apply to `card`, whose message keeps scrolling for
exactly that long.

### `card`

One line of text is enough for a headline and too little for an alert. A card
puts the whole event on the panel at once:

```
 y  0- 7   header bar in the severity colour, system name in 4x6, dark
 y 10-33   title, word-wrapped: 5x7 on 3 lines (12 chars each),
           or 4x6 on 4 lines (16 chars each) when it does not fit in 5x7
 y 36-45   message in 6x10, in the severity colour, scrolling
 y 48-53   tags in 4x6, dimmed — only when the message has tags
 y 55      divider, dimmed
 y 58-63   footer in 4x6, dimmed: author left, HH:MM right
```

```
+----------------------------------------------------------------+
|PROMETHEUS##########(severity colour)###########################|  0-7
|                                                                |
|pod crash                                                       |  10-16
|loop                                                            |  18-24
|detected                                                        |  26-32
|                                                                |
|pod prometheus-0 restarted 5 times in  <-- scrolls              |  36-45
|                                                                |
|k8s monitoring                                                  |  48-53
|----------------------------------------------------------------|  55
|ops-bot                                                    14:34|  58-63
+----------------------------------------------------------------+
```

- **Title**: the message title, or the message body when there is no title.
  It gets the larger 5x7 font when all of it fits on three lines, and 4x6 on
  four lines otherwise. What still does not fit ends in an ellipsis (`…`), and
  a word longer than a line is broken where it has to be.
- **`strip_prefix: true`** drops a leading `[SEVERITY]` (`[ERROR]`,
  `[warning]`, …) and then a leading system name from the title, so
  `[ERROR] prometheus pod crash loop detected` from system `prometheus` is
  shown as `pod crash loop detected`: the header already says both, and the
  panel is too narrow to say them twice. Only the message's own system name is
  stripped, as a whole word, and a title that would be left empty is kept.
- **Scrolling line**: the message body, or the rule's `text` rendered as a
  template when the rule has one. It scrolls at the same 1px / 30 ms as
  `text`, starting readable at the left edge and re-entering from the right,
  for the whole `duration`. A message that fits the panel (up to 10
  characters) stands still. At 30 ms a pixel, a pass of a message takes about
  0.18 s a character plus 2 s, so give long messages a long enough `duration`
  — the card ends when the duration does, not when the pass does.
- **Time**: the message's `timestamp` (RFC 3339) in the catcher's local time
  — the `TZ` environment variable, or the host's zone. A message without a
  usable timestamp shows when the catcher received it.
- **`hold: true`** keeps the card up until the next display replaces it, the
  message still scrolling; a replacement takes over at once.
- **Colour**: only the header bar and the scrolling line take the severity
  colour. The title is near-white and the rest dimmed, so the colour says
  "what kind of event" and the text stays readable.
- `font` is ignored: the layout is fixed, like the scoreboard's, and drawn for
  a 64x64 panel. Other sizes are warned about and get the same rows from the
  top.

```yaml
error-all:
  systems: ["*"]
  severity: [ERROR, CRITICAL]
  kind: card
  strip_prefix: true
  duration: 10
```

`hack/render_card.py` renders a card to a PNG without a panel, through the
same rule matching and drawing code:

```bash
python hack/render_card.py --out card.png
python hack/render_card.py --severity warning --system argocd \
  --title "[WARNING] argocd app out of sync" --message "homerun2-dev OutOfSync" --out warn.png
```

The simulator draws cards from the layout the panel computes, so the two agree
to the pixel. The `/display` API does not offer `card`: a card is built from a
message's fields, which a `POST /display` body does not have.

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

1. During quiet hours, a message whose severity is not in `quietHours.allow`
   matches no rule (see below)
2. Rules are evaluated in order (first match wins)
3. A rule matches if the message's system is in the rule's `systems` list (or `"*"`)
4. AND the message's severity is in the rule's `severity` list
5. AND every entry of the rule's `tags` equals a whole element of the message's
   comma-separated `tags` (`issue_comment` matches `github,issue_comment,org/repo`
   but not `issue_comment_edited`). Case-sensitive, the same as light-catcher's
   `tags`, so one profile reads the same on both catchers. No `tags`: no filter
6. If no rule matches, the message is not displayed on the matrix and is
   logged at `info` level saying so. In the web simulator it is still recorded,
   coloured by its severity rather than rendered by a rule.

Cover every severity your producers actually emit. Until 2026-09-05 the shipped
default had no rule for `ERROR` or `CRITICAL`: on the matrix those were dropped
(logged at `debug`, invisible at the default `LOG_LEVEL`) and in the simulator
they were recorded in the same blue as `INFO`. A failing alert looked exactly
like a working one.

## Quiet hours

`quietHours` keeps the panel calm outside office hours: from `from` to `to`
(it may cross midnight), and all of Saturday and Sunday with `weekends: true`,
only the `allow` severities (default `error`, `critical`) are shown. Everything
else matches no rule: it is not displayed, it is logged, and the web simulator
still lists it like any unmatched message. The idle screen is unaffected.

Times are `HH:MM` in `timezone` (an IANA name; unset means the host's local
time). Quote them: unquoted, YAML reads `19:00` as the number 1140. The
catcher reads that back as 19:00, but a quoted time says what it means.

```yaml
quietHours:
  from: "19:00"
  to: "07:00"
  weekends: true
  timezone: Europe/Berlin
  allow: [error, critical]
```

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
| `{{ timestamp }}` | When the event happened, as the producer sent it (RFC 3339) |

The `localtime` filter turns a timestamp into the catcher's local time
(`TZ`), `HH:MM` unless given a `strftime` format. A value it cannot parse is
passed through unchanged.

```yaml
text: "{{ system }} {{ timestamp | localtime }}: {{ title }}"   # prometheus 14:34: ...
text: "{{ timestamp | localtime('%d.%m. %H:%M') }}"             # 28.09. 14:34
```

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
    kind: card
    strip_prefix: true
    duration: 10

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
