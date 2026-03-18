"""
Main entry point for homerun2-led-catcher Kubernetes deployment
Imports all resources and exports manifests
"""

import namespace
import serviceaccount
import configmap
import profile_configmap
import secret
import deploy
import service
import httproute

# Export all manifests (filter out None values)
manifests = [
    m for m in [
        namespace.namespace
        serviceaccount.serviceAccount
        configmap.configMap
        profile_configmap.profileConfigMap
        secret.secretRedis
        deploy.deployment
        service.service
        httproute.httproute
    ] if m
]
