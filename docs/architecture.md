# Architecture

## Overview

homerun2-led-catcher is a Python microservice that consumes messages from Redis Streams and displays them on a 64x64 RGB LED matrix (via rpi-rgb-led-matrix) with an embedded HTMX web simulator for development and demos.

## Data Flow

```
Redis Streams (messages)
       │
       ├─→ XREADGROUP (consumer group)
       │
       ├─→ Extract messageID from stream entry
       │
       ├─→ JSON.GET messageID (resolve full payload)
       │
       ├─→ Parse into Message dataclass
       │
       ├─→ log_handler: structured JSON logging (severity-aware)
       │
       ├─→ led_handler: match profile rule → display on LED matrix
       │         │
       │         ├─→ static text (centered, timed)
       │         ├─→ scrolling text (left to right)
       │         ├─→ ticker (multi-pass scroll)
       │         ├─→ image (PNG/JPG scaled to 64x64)
       │         └─→ gif (animated, frame-timed)
       │
       ├─→ web_handler: record event → HTMX simulator
       │         │
       │         ├─→ EventTracker (ring buffer, 100 events)
       │         ├─→ SSE /api/events/stream (real-time updates)
       │         ├─→ Canvas LED grid (64x64 pixel simulation)
       │         └─→ Event timeline sidebar
       │
       └─→ XACK (acknowledge processed message)
```

## Components

| Package | Description |
|---------|-------------|
| `consumer/` | Async Redis Streams consumer with consumer groups |
| `handlers/` | log_handler, led_handler, web_handler, health endpoint |
| `models/` | Message and CaughtMessage dataclasses |
| `config/` | Environment variable loading, JSON log formatter |
| `profile/` | YAML profile loading, rule matching, Jinja2 templating |
| `display/` | rpi-rgb-led-matrix wrapper, display modes |
| `web/` | FastAPI HTMX simulator, SSE, EventTracker |

## Operating Modes

| Mode | Handlers Active | Use Case |
|------|----------------|----------|
| `led` | log + led | Raspberry Pi hardware only |
| `web` | log + web | Development, Kubernetes, demos |
| `full` | log + led + web | Pi with dashboard |

## Profile/Rules Engine

Messages are routed to display modes using YAML profiles with first-match semantics:

1. Iterate rules in order
2. Match message system against rule systems (or wildcard `*`)
3. Match message severity against rule severity list
4. First match → resolve display config (Jinja2 templating, severity color)
5. No match → skip display

## CI/CD Pipeline

```
Pre-commit hook (local)
    │
    ├─→ ruff check (lint)
    ├─→ ruff format --check
    └─→ pytest (28+ tests)

GitHub Actions (remote)
    │
    ├─→ Dagger: python lint
    ├─→ Dagger: python format-check
    ├─→ Dagger: python test
    ├─→ Dagger: python security-scan (bandit)
    └─→ Dagger: docker build → push ttl.sh → trivy scan
```

## Deployment

KCL manifests generate Kubernetes resources:
- Deployment (rolling update, security hardening, health probes)
- Service (ClusterIP)
- ConfigMap, Secret, ServiceAccount, Namespace
- HTTPRoute (Gateway API, optional)
