"""Connect upstream oauth2-proxy to a loopback-only onboarding portal."""

import hashlib

import pulumi
import pulumi_kubernetes as k8s

import settings as s


def deploy_sample(
    provider: k8s.Provider,
    issuer: k8s.core.v1.Service,
    release: k8s.helm.v3.Release,
) -> k8s.apps.v1.Deployment:
    """Deploy a confidential OIDC client without writing an OAuth implementation."""
    options = pulumi.ResourceOptions(provider=provider, depends_on=[release])
    source = (s.ROOT / "sample/portal.py").read_text()
    script = k8s.core.v1.ConfigMap(
        "portal", metadata={"name": "onboarding-portal", "namespace": s.namespace},
        data={"portal.py": source}, opts=options,
    )
    credentials = k8s.core.v1.Secret(
        "client", metadata={"name": "onboarding-client", "namespace": s.namespace},
        string_data={
            "OAUTH2_PROXY_CLIENT_SECRET": s.client_secret,
            "OAUTH2_PROXY_COOKIE_SECRET": s.cookie_key,
        }, opts=options,
    )
    revision = pulumi.Output.all(s.client_secret, s.cookie_key).apply(
        lambda values: hashlib.sha256((source + "\0".join(values)).encode()).hexdigest(),
    )
    deployment = k8s.apps.v1.Deployment(
        "sample", metadata={"name": "onboarding-sample", "namespace": s.namespace},
        spec={
            "replicas": 1,
            "selector": {"matchLabels": {"app": "onboarding-sample"}},
            "template": {
                "metadata": {
                    "labels": {"app": "onboarding-sample"},
                    "annotations": {"onboarding-revision": revision},
                },
                "spec": {
                    "automountServiceAccountToken": False,
                    "hostAliases": [{
                        "ip": issuer.spec.cluster_ip,
                        "hostnames": ["authentik.localhost"],
                    }],
                    "securityContext": {
                        "runAsNonRoot": True, "runAsUser": 10001, "runAsGroup": 10001,
                        "seccompProfile": {"type": "RuntimeDefault"},
                    },
                    "containers": [
                        {
                            "name": "oauth2-proxy",
                            "image": "quay.io/oauth2-proxy/oauth2-proxy:v7.15.6@sha256:c3415e8660af553f5e18f5d90db050614c02493c0ac6260ac59442f6d9b12048",
                            "args": [
                                "--provider=oidc",
                                "--oidc-issuer-url=http://authentik.localhost:9000/application/o/onboarding/",
                                "--client-id=onboarding",
                                "--redirect-url=http://localhost:4180/oauth2/callback",
                                "--http-address=0.0.0.0:4180",
                                "--upstream=http://127.0.0.1:8080/",
                                "--scope=openid profile email",
                                "--allowed-group=onboarding-members",
                                "--email-domain=onboarding.example.test",
                                "--code-challenge-method=S256",
                                "--insecure-oidc-skip-nonce=false",
                                "--cookie-secure=false",
                                "--cookie-samesite=lax",
                                "--cookie-expire=5m",
                                "--cookie-refresh=0",
                                "--pass-basic-auth=false",
                                "--pass-user-headers=true",
                                "--pass-access-token=false",
                                "--skip-provider-button=true",
                                "--request-logging=false",
                            ],
                            "envFrom": [{"secretRef": {"name": "onboarding-client"}}],
                            "ports": [{"name": "http", "containerPort": 4180}],
                            "readinessProbe": {
                                "httpGet": {"path": "/ready", "port": 4180},
                                "periodSeconds": 5,
                            },
                            "securityContext": {
                                "allowPrivilegeEscalation": False,
                                "readOnlyRootFilesystem": True,
                                "capabilities": {"drop": ["ALL"]},
                            },
                            "resources": {
                                "requests": {"cpu": "50m", "memory": "64Mi"},
                                "limits": {"memory": "128Mi"},
                            },
                        },
                        {
                            "name": "portal",
                            "image": "docker.io/library/python:3.12.15-slim-bookworm@sha256:6a0e06f5ba0b373d051aabd3fffb9b873f27f9a2a3a14bcbe37dee6f0e3bbc70",
                            "command": ["python", "-B", "/app/portal.py"],
                            "volumeMounts": [{
                                "name": "portal", "mountPath": "/app", "readOnly": True,
                            }],
                            "securityContext": {
                                "allowPrivilegeEscalation": False,
                                "readOnlyRootFilesystem": True,
                                "capabilities": {"drop": ["ALL"]},
                            },
                            "resources": {
                                "requests": {"cpu": "25m", "memory": "32Mi"},
                                "limits": {"memory": "64Mi"},
                            },
                        },
                    ],
                    "volumes": [{"name": "portal", "configMap": {"name": "onboarding-portal"}}],
                },
            },
        },
        opts=pulumi.ResourceOptions(
            provider=provider, depends_on=[release, script, credentials, issuer],
        ),
    )
    k8s.core.v1.Service(
        "sample", metadata={"name": "onboarding-sample", "namespace": s.namespace},
        spec={"type": "ClusterIP", "selector": {"app": "onboarding-sample"},
              "ports": [{"name": "http", "port": 4180, "targetPort": 4180}]},
        opts=options,
    )
    return deployment
