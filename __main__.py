"""Deploy private Authentik and its connected OIDC onboarding application."""

import hashlib
import json

import pulumi
import pulumi_kubernetes as k8s

import data_services as data
import settings as s
from relying_party import deploy_sample

chart_path = s.ROOT / "vendor/authentik-2026.8.5.tgz"
if hashlib.sha256(chart_path.read_bytes()).hexdigest() != (
    "d00e8addefb2c758e071bd6eb8886f99be04eeba15c26ac869027c3d89d0a75e"
):
    raise s.ConfigurationError("vendored chart digest")
ca = (s.ROOT / "config/rds-ca.pem").read_text()
if hashlib.sha256(ca.encode()).hexdigest() != (
    "d46e1bdfda05c8e7644e50930806a19b139a222542bf0348082fb59ece2b5fa5"
):
    raise s.ConfigurationError("regional RDS CA digest")

kubeconfig = json.dumps({
    "apiVersion": "v1", "kind": "Config", "current-context": "eks",
    "clusters": [{"name": "eks", "cluster": {
        "server": data.cluster.endpoint,
        "certificate-authority-data": data.cluster.certificate_authorities[0].data,
    }}],
    "contexts": [{"name": "eks", "context": {"cluster": "eks", "user": "eks"}}],
    "users": [{"name": "eks", "user": {"exec": {
        "apiVersion": "client.authentication.k8s.io/v1beta1",
        "command": "aws", "args": [
            "eks", "get-token", "--cluster-name", s.cluster_name,
            "--region", s.REGION,
        ],
    }}}],
})
provider = k8s.Provider("eks", kubeconfig=pulumi.Output.secret(kubeconfig))
namespace = k8s.core.v1.Namespace(
    "namespace", metadata={"name": s.namespace},
    opts=pulumi.ResourceOptions(provider=provider),
)
options = pulumi.ResourceOptions(provider=provider, depends_on=[namespace])
service_account = k8s.core.v1.ServiceAccount(
    "authentik", metadata={
        "name": "authentik", "namespace": s.namespace,
        "annotations": {"eks.amazonaws.com/role-arn": data.role.arn},
    }, automount_service_account_token=False, opts=options,
)
blueprint = (s.ROOT / "config/onboarding.yaml").read_text()
blueprints = k8s.core.v1.ConfigMap(
    "blueprints", metadata={"name": "onboarding-blueprints", "namespace": s.namespace},
    data={"onboarding.yaml": blueprint}, opts=options,
)
certificates = k8s.core.v1.ConfigMap(
    "certificates", metadata={"name": "rds-ca", "namespace": s.namespace},
    data={"rds-ca.pem": ca}, opts=options,
)
runtime = k8s.core.v1.Secret(
    "runtime", metadata={"name": "authentik-runtime", "namespace": s.namespace},
    string_data={
        "AUTHENTIK_SECRET_KEY": s.secret_key,
        "AUTHENTIK_POSTGRESQL__HOST": data.database.address,
        "AUTHENTIK_POSTGRESQL__NAME": "authentik",
        "AUTHENTIK_POSTGRESQL__USER": "authentik",
        "AUTHENTIK_POSTGRESQL__PASSWORD": s.db_password,
        "AUTHENTIK_POSTGRESQL__SSLMODE": "verify-full",
        "AUTHENTIK_POSTGRESQL__SSLROOTCERT": "/etc/rds/rds-ca.pem",
        "AUTHENTIK_STORAGE__BACKEND": "s3",
        "AUTHENTIK_STORAGE__S3__BUCKET_NAME": data.bucket.id,
        "AUTHENTIK_STORAGE__S3__REGION": s.REGION,
        "AUTHENTIK_STORAGE__S3__CUSTOM_DOMAIN": "",
        "AUTHENTIK_ERROR_REPORTING__ENABLED": "false",
    }, opts=options,
)
onboarding = k8s.core.v1.Secret(
    "onboarding", metadata={"name": "authentik-onboarding", "namespace": s.namespace},
    string_data={
        "AUTHENTIK_BOOTSTRAP_PASSWORD": s.bootstrap_password,
        "ALICE_PASSWORD": s.alice_password, "BOB_PASSWORD": s.bob_password,
        "OIDC_CLIENT_SECRET": s.client_secret,
    }, opts=options,
)
revision = pulumi.Output.all(
    s.secret_key, s.db_password, s.bootstrap_password, s.alice_password,
    s.bob_password, s.client_secret,
).apply(lambda values: hashlib.sha256(
    (blueprint + "\0".join(values)).encode(),
).hexdigest())
release = k8s.helm.v3.Release(
    "authentik", name="authentik", namespace=s.namespace, chart=str(chart_path),
    version="2026.8.5", dependency_update=False, atomic=True, timeout=900,
    values={
        "global": {
            "image": {
                "repository": "ghcr.io/goauthentik/server", "tag": "2026.8.5",
                "digest": "sha256:602227a86455d6772e3d16b7d852713c4f864f40df5df894abd1285bb53979a5",
            },
            "podAnnotations": {"onboarding-revision": revision},
            "volumes": [{"name": "rds-ca", "configMap": {"name": "rds-ca"}}],
            "volumeMounts": [{"name": "rds-ca", "mountPath": "/etc/rds", "readOnly": True}],
        },
        "authentik": {"enabled": False, "existingSecret": {"secretName": "authentik-runtime"}},
        "postgresql": {"enabled": False},
        "serviceAccount": {"create": False},
        "blueprints": {"configMaps": ["onboarding-blueprints"]},
        "server": {
            "replicas": s.server_replicas, "serviceAccountName": "authentik",
            "automountServiceAccountToken": False,
            "resources": {"requests": {"cpu": "250m", "memory": "512Mi"},
                          "limits": {"memory": "1Gi"}},
        },
        "worker": {
            "replicas": s.worker_replicas, "serviceAccountName": "authentik",
            "automountServiceAccountToken": False,
            "envFrom": [{"secretRef": {"name": "authentik-onboarding"}}],
            "resources": {"requests": {"cpu": "250m", "memory": "512Mi"},
                          "limits": {"memory": "1Gi"}},
        },
    },
    opts=pulumi.ResourceOptions(
        provider=provider, depends_on=[
            namespace, service_account, runtime, onboarding, blueprints,
            certificates, data.file_permissions, data.public_access,
            data.encryption, data.versioning, data.transport_policy, data.cors,
        ],
    ),
)
# A stable same-origin bridge: browser loopback and pod hostAliases use this hostname.
issuer_service = k8s.core.v1.Service(
    "issuer", metadata={"name": "authentik-loopback", "namespace": s.namespace},
    spec={
        "type": "ClusterIP",
        "selector": {"app.kubernetes.io/name": "authentik",
                     "app.kubernetes.io/instance": "authentik",
                     "app.kubernetes.io/component": "server"},
        "ports": [{"name": "http", "port": 9000, "targetPort": 9000}],
    }, opts=options,
)
sample = deploy_sample(provider, issuer_service, release)
pulumi.export("namespace", namespace.metadata.name)
pulumi.export("authentikUrl", "http://authentik.localhost:9000")
pulumi.export("sampleUrl", "http://localhost:4180")
pulumi.export("databaseId", data.database.id)
pulumi.export("fileBucket", data.bucket.id)
