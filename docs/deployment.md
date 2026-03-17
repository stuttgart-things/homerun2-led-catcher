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

### With Redis Password

```yaml
config.redisPassword: my-secret-password
```

This creates a Kubernetes Secret and references it in the Deployment.

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
