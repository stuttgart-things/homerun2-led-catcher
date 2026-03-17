# Profile Reference

## Overview

Display profiles define how messages are rendered on the LED matrix. Each profile is a YAML file with display rules and color definitions.

## Schema

```yaml
displayRules:
  <rule-name>:
    systems: [<system1>, <system2>, ...]  # or ["*"] for wildcard
    severity: [<severity1>, ...]           # ERROR, WARNING, INFO, SUCCESS, DEBUG
    kind: <display-mode>                   # static, text, ticker, image, gif
    text: "<jinja2-template>"              # for text modes
    image: "<filename>"                    # for image/gif modes
    font: "<font-file>"                    # BDF font file
    duration: <seconds>                    # display duration

colors:
  error: [255, 0, 0]
  warning: [255, 165, 0]
  success: [0, 255, 0]
  info: [0, 100, 255]
  debug: [128, 128, 128]
```

## Display Modes

| Mode | Description | Required Fields |
|------|-------------|-----------------|
| `static` | Centered text, fixed duration | `text`, `duration` |
| `text` | Scrolling text, left to right | `text` |
| `ticker` | Multi-pass scrolling text | `text` |
| `image` | Static PNG/JPG, scaled to 64x64 | `image`, `duration` |
| `gif` | Animated GIF playback | `image`, `duration` |

## Rule Matching

1. Rules are evaluated in order (first match wins)
2. A rule matches if the message's system is in the rule's `systems` list (or `"*"`)
3. AND the message's severity is in the rule's `severity` list
4. If no rule matches, the message is skipped (logged but not displayed)

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
    font: myfont.bdf
    duration: 3

  warning-all:
    systems: ["*"]
    severity: [WARNING]
    kind: text
    text: "{{ system }}: {{ title }}"
    font: myfont.bdf
    duration: 5

  default-info:
    systems: ["*"]
    severity: [INFO, SUCCESS]
    kind: text
    text: "{{ system }}: {{ title }}"
    font: myfont.bdf
    duration: 5

colors:
  error: [255, 0, 0]
  warning: [255, 165, 0]
  success: [0, 255, 0]
  info: [0, 100, 255]
```
