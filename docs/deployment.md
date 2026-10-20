# Deployment

## Docker

```bash
# Build
docker build -t homerun2-led-catcher:local .

# Run (web mode)
docker run -p 8080:8080 \
  -e LED_MODE=web \
  -e REDIS_ADDR=host.docker.internal \
  homerun2-led-catcher:local
```

## Kubernetes (KCL)

### Render Manifests

```bash
# With inline parameters
kcl run kcl/ \
  -D config.image=ghcr.io/stuttgart-things/homerun2-led-catcher:latest \
  -D config.namespace=homerun2 \
  -D config.redisAddr=redis-stack.homerun2.svc.cluster.local

# With deploy profile
kcl run kcl/ -y tests/kcl-deploy-profile.yaml
```

### Deploy Profile Example

```yaml
config.image: ghcr.io/stuttgart-things/homerun2-led-catcher:latest
config.namespace: homerun2
config.redisAddr: redis-stack.homerun2.svc.cluster.local
config.redisPort: "6379"
config.redisStream: messages
config.consumerGroup: homerun2-led-catcher
config.ledMode: web
config.healthPort: "8080"
```

### With HTTPRoute (Gateway API)

```yaml
config.image: ghcr.io/stuttgart-things/homerun2-led-catcher:latest
config.namespace: homerun2
config.redisAddr: redis-stack.homerun2.svc.cluster.local
config.ledMode: web
config.httpRouteEnabled: true
config.httpRouteParentRefName: my-gateway
config.httpRouteParentRefNamespace: default
config.httpRouteHostname: led-catcher.example.com
```

### Under a path prefix (`BASE_PATH`)

To share a hostname with the other homerun2 UIs — the homerun2 portal, or one
gateway for all of them — serve the service under a prefix:

```yaml
config.basePath: /led
config.httpRouteEnabled: true
config.httpRouteParentRefName: my-gateway
config.httpRouteHostname: homerun2.example.com
```

The HTTPRoute then matches `PathPrefix /led` and forwards it without a
rewrite: the service answers on `/led/`, `/led/api/...` and `/led/display`
itself, and every URL in the simulator page carries the prefix. `/` redirects
to `/led/`. `/healthz` and `/health` answer at the root as well, so the probes
need no change.

Behind the portal, the service also offers its piece of the portal's
overview: `/led/ui/manifest.json` lists the fragment `/led/ui/fragment/events`
(the latest events on the matrix), which the portal loads into the LED card
every 5 seconds. It is bare HTML styled by the portal's `hr-*` classes; the
contract is described in the homerun2-portal README.

The simulator's colours are the homerun2 design tokens (`--hr-*`). The page
carries a copy of them; when a request comes through the portal (it sets
`X-Forwarded-Prefix`), the page also links the portal's
`/static/homerun2-tokens.css`, so a palette change in the portal reaches the
simulator as well. The `/wall` view keeps its black background.

### With Redis Password

```yaml
config.redisPassword: my-secret-password
```

This creates a Kubernetes Secret and references it in the Deployment.

### With the `/display` API

```yaml
config.apiToken: my-api-token
```

This creates the Secret `<name>-api`, which the Deployment reads into `LED_API_TOKEN`
(`optional: true`, like the Redis password). Without a token the write endpoints of
`/display` are not registered. See [Display API](display-api.md).

### Standalone (no Redis)

```yaml
config.ledMode: standalone
config.apiToken: my-api-token
```

The simulator is driven only by the `/display` API, and nothing connects to Redis.
In a cluster there is no panel, so this is a demo or a place to try the API. The
Redis settings can stay; they are ignored.

### Apply

```bash
kcl run kcl/ -y tests/kcl-deploy-profile.yaml | kubectl apply -f -
```

## Flux GitOps

Add a Kustomization in your Flux apps directory pointing to the KCL-rendered manifests, or use the OCI kustomize pattern used by other homerun2 services.

## Security

The Deployment includes hardened security settings:

- `readOnlyRootFilesystem: true`
- `runAsNonRoot: true` (user 65532)
- `allowPrivilegeEscalation: false`
- All capabilities dropped
- Seccomp profile: RuntimeDefault
- Pod anti-affinity for distribution across nodes
