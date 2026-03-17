"""
HTTPRoute (Gateway API) for homerun2-led-catcher
"""

import labels

config = labels.config

_hostname = config.httpRouteHostname

httproute = {
    apiVersion: "gateway.networking.k8s.io/v1"
    kind: "HTTPRoute"
    metadata: {
        name: config.name
        namespace: config.namespace
        labels: labels.commonLabels
        if config.annotations or config.httpRouteAnnotations:
            annotations: {
                **config.annotations
                **config.httpRouteAnnotations
            }
    }
    spec: {
        parentRefs: [
            {
                name: config.httpRouteParentRefName
                if config.httpRouteParentRefNamespace:
                    namespace: config.httpRouteParentRefNamespace
            }
        ]
        if _hostname:
            hostnames: [_hostname]
        rules: [
            {
                matches: [
                    {
                        path: {
                            type: "PathPrefix"
                            value: "/"
                        }
                    }
                ]
                backendRefs: [
                    {
                        name: config.name
                        port: config.servicePort
                    }
                ]
            }
        ]
    }
} if config.httpRouteEnabled else None
