"""
Schema definition for homerun2-led-catcher deployment configuration
"""

schema LedCatcher:
    """Configuration for homerun2-led-catcher Kubernetes deployment"""

    # Metadata
    name: str = "homerun2-led-catcher"
    namespace: str = "homerun2"

    # Image
    image: str = "ghcr.io/stuttgart-things/homerun2-led-catcher:latest"
    imagePullPolicy: str = "IfNotPresent"

    # Replicas and resources
    replicas: int = 1
    cpuRequest: str = "50m"
    cpuLimit: str = "200m"
    memoryRequest: str = "64Mi"
    memoryLimit: str = "256Mi"

    # Redis
    redisAddr: str = "redis-stack.homerun2.svc.cluster.local"
    redisPort: int | str = "6379"
    redisStream: str = "messages"

    # Consumer group
    consumerGroup: str = "homerun2-led-catcher"

    # LED mode
    ledMode: str = "web"

    # Health endpoint
    healthPort: int | str = "8080"

    # Service
    serviceType: str = "ClusterIP"
    servicePort: int = 80
    containerPort: int = 8080

    # HTTPRoute (Gateway API)
    httpRouteEnabled: bool = False
    httpRouteParentRefName: str = ""
    httpRouteParentRefNamespace: str = ""
    httpRouteHostname: str = ""
    httpRouteAnnotations: {str:str} = {}

    # Secrets
    redisPassword: str = ""

    # Profile path inside container
    profilePath: str = "/config/profile.yaml"

    # Profile data (display rules YAML content)
    profileData: str = """\
displayRules:
  error-all:
    systems: ["*"]
    severity: [ERROR, CRITICAL]
    kind: text
    text: "{{ system }}: {{ title }}"
    font: 6x10.bdf
    duration: 5
  warning-all:
    systems: ["*"]
    severity: [WARNING]
    kind: text
    text: "{{ system }}: {{ title }}"
    font: 6x10.bdf
    duration: 5
  # A live score, not a notification: it should stay readable between points
  # rather than scroll past once. `hold` keeps it up until the next point
  # replaces it, and `static` renders the title alone — the panel is 64x64 and
  # a "tabletennis: " prefix would push the score off the edge.
  #
  # Must stay above default-info: first match wins, and the wildcard there
  # would otherwise take it.
  #
  # Fed by stuttgart-things/zaehlwerk, which owns the running match and pitches
  # `system: tabletennis` on its own stream (zaehlwerk ADR-0003). `duration`
  # only applies on a catcher older than v0.5.1, which ignores `hold`.
  tabletennis-score:
    systems: [tabletennis]
    severity: [INFO, SUCCESS]
    kind: static
    text: "{{ title }}"
    font: 6x10.bdf
    hold: true
    duration: 3
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
  debug: [128, 128, 128]
"""

    # Extra environment variables
    extraEnvVars: {str:str} = {}

    # Labels and annotations
    labels: {str:str} = {}
    annotations: {str:str} = {}

    check:
        replicas >= 0, "replicas must be >= 0"
        servicePort >= 1 and servicePort <= 65535, "servicePort must be valid"
        containerPort >= 1 and containerPort <= 65535, "containerPort must be valid"
        name != "", "name must not be empty"
        namespace != "", "namespace must not be empty"
        ledMode in ["led", "web", "full"], "ledMode must be led, web, or full"
