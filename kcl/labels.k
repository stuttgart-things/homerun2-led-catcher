"""
Common labels for homerun2-led-catcher resources
"""

import schema

# Get command-line options
_image = option("config.image")
_namespace = option("config.namespace")
_redisAddr = option("config.redisAddr")
_redisPort = option("config.redisPort")
_redisStream = option("config.redisStream")
_consumerGroup = option("config.consumerGroup")
_redisPassword = option("config.redisPassword")
_healthPort = option("config.healthPort")
_ledMode = option("config.ledMode")
_httpRouteEnabled = option("config.httpRouteEnabled")
_httpRouteParentRefName = option("config.httpRouteParentRefName")
_httpRouteParentRefNamespace = option("config.httpRouteParentRefNamespace")
_httpRouteHostname = option("config.httpRouteHostname")
_httpRouteAnnotations = option("config.httpRouteAnnotations") or {}
_extraEnvVars = option("config.extraEnvVars") or {}

# Config instance with command-line overrides
config: schema.LedCatcher = schema.LedCatcher {
    if _image:
        image = _image
    if _namespace:
        namespace = _namespace
    if _redisAddr:
        redisAddr = _redisAddr
    if _redisPort:
        redisPort = _redisPort
    if _redisStream:
        redisStream = _redisStream
    if _consumerGroup:
        consumerGroup = _consumerGroup
    if _redisPassword:
        redisPassword = _redisPassword
    if _healthPort:
        healthPort = _healthPort
    if _ledMode:
        ledMode = _ledMode
    if _httpRouteEnabled:
        httpRouteEnabled = _httpRouteEnabled in [True, "True", "true"]
    if _httpRouteParentRefName:
        httpRouteParentRefName = _httpRouteParentRefName
    if _httpRouteParentRefNamespace:
        httpRouteParentRefNamespace = _httpRouteParentRefNamespace
    if _httpRouteHostname:
        httpRouteHostname = _httpRouteHostname
    if _httpRouteAnnotations:
        httpRouteAnnotations = _httpRouteAnnotations
    if _extraEnvVars:
        extraEnvVars = _extraEnvVars
}

# Common labels applied to all resources
commonLabels = {
    "app.kubernetes.io/name": config.name
    "app.kubernetes.io/instance": config.name
    "app.kubernetes.io/component": "led-catcher"
    "app.kubernetes.io/part-of": "homerun2"
    **config.labels
}

# Selector labels for pods
selectorLabels = {
    "app.kubernetes.io/name": config.name
    "app.kubernetes.io/instance": config.name
}
