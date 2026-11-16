# Authentik OIDC onboarding on Amazon EKS with Python Pulumi

Deploy a private Authentik identity service and a working team onboarding portal
on an existing EKS cluster in `us-east-2`. Pulumi connects encrypted RDS PostgreSQL,
private S3 file storage, a scoped IRSA role, declarative users/application/client
and a relying party protected by oauth2-proxy.

**Validation status, November 15, 2026:** deployed in an approved disposable AWS
environment and deleted with independent resource cleanup checks. In separate
clean Chrome profiles, Alice reached the protected checklist with HTTP 200 and
all three items; Bob was denied application access and reached his authenticated
Authentik user settings. Spoofed identity headers redirected to login, and actual
pod binding and Services confirmed the backend was private.

One acceptance assertion failed: the pinned upstream oauth2-proxy returns HTTP
500 for a malformed callback state, rather than the required 4xx. It did not
return the protected page. Persistence, scaling and recovery remain untested.
This demonstration makes no production readiness or HA claim.

## What runs

The official Authentik Helm chart runs one server and one worker. PostgreSQL stores
identities, keys, sessions and background coordination; this release needs no Redis.
RDS is private, encrypted, backed up for seven days and accessed using verified
TLS with the included regional AWS CA bundle. S3 stores uploaded files privately
using short-lived IRSA credentials, with encryption, versioning and public access
blocked.

The worker mounts `config/onboarding.yaml` during normal startup. This supported
blueprint creates `alice`, `bob`, `onboarding-members`, an authorization-code-only
confidential OIDC client and an application bound to that group. Only Alice is a
member. Secrets arrive through worker environment references, not YAML literals.
No bootstrap API token or additional provisioning command is required.

The sample pod contains upstream oauth2-proxy and `sample/portal.py`. The proxy
performs OIDC discovery, signature/issuer/audience/nonce/state validation, PKCE
S256 and group checks. The small Python page displays the authenticated identity
and a first-day checklist. Its backend binds only `127.0.0.1:8080` inside the pod;
only proxy port 4180 has a Service.

This uses official blueprints rather than an AnyTF-generated Authentik provider:
there is no long-lived elevated API token, generated SDK, API-to-Pulumi bootstrap
cycle or second provisioning phase. Removing a blueprint file does not remove
its objects. Selective offboarding uses blueprint `state: absent`; destroying
this entire stack removes the instance holding its objects.

## Requirements

- Existing EKS with Linux AMD64 nodes, network access to private RDS subnets,
  public container registries, S3 and regional STS HTTPS APIs.
- Existing EKS IAM OIDC provider and IRSA support. The stack creates only its
  workload role and service account, not shared cluster infrastructure.
- Two or more existing private subnets spanning availability zones in the same
  VPC, and the actual security group used by the cluster's nodes. If using
  security groups for pods, adapt the database source to the actual pod group
  before deployment; do not guess a source group.
- Scoped AWS provisioning credentials, EKS namespace access, AWS CLI v2,
  CPython 3.12, uv, Pulumi CLI 3.273.0 and providers AWS 7.52.1/Kubernetes 4.34.2.
  The lock supports the reviewed ARM64 runner and adds canonical Linux AMD64 hashes.
- A browser machine that can run loopback-only port-forwards to that cluster.

No Tailscale, operator, CSI driver, custom runner, cluster provisioning or
workstation workload execution is part of this example. Review and scan the exact
artifacts before running these commands in your approved execution environment.

## Configure and deploy

Clone the private repository with an account that has access, then configure it
in your approved execution environment:

```sh
git clone https://github.com/pulumi-labs/pulumi-example-stack-aws-authentik-eks.git
cd pulumi-example-stack-aws-authentik-eks
```

From that directory:

```sh
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python --require-hashes --only-binary :all: -r requirements.lock
pulumi stack init demo
pulumi config set accountId "$AWS_ACCOUNT_ID"
pulumi config set clusterName "$EKS_CLUSTER_NAME"
pulumi config set vpcId "$VPC_ID"
pulumi config set nodeSecurityGroupId "$EKS_NODE_SECURITY_GROUP_ID"
pulumi config set privateSubnetIds "$PRIVATE_SUBNET_IDS"
```

`PRIVATE_SUBNET_IDS` is a comma-separated list of subnet IDs, without spaces.
Use a lowercase stack name of at most 24 characters. Set `AWS_PROFILE` or scoped
temporary credential environment variables in the execution environment; do not
place credentials in this directory. The generated kubeconfig uses AWS CLI
`eks get-token`, the selected cluster's endpoint and its CA.

`resourcePrefix` optionally sets the namespace and prefixes owned IAM role,
database, subnet group, security group and bucket names. It defaults to
`authentik-<stack>` and accepts a lowercase DNS label up to 40 characters.
Every taggable owned AWS
resource receives `pulumi-project` and `pulumi-stack` through the explicit
provider. Optional `permissionsBoundaryArn` applies to the application IAM role;
an IAM boundary is not mandatory for ordinary product use.

Enter distinct, password-manager-generated secrets through interactive prompts.
Each of the following must contain 32 to 128 URL-safe letters, digits, `_` or `-`:

```sh
pulumi config set --secret databasePassword
pulumi config set --secret secretKey
pulumi config set --secret bootstrapPassword
pulumi config set --secret alicePassword
pulumi config set --secret bobPassword
pulumi config set --secret clientSecret
```

Keep Alice and Bob's values securely for the browser checks. `bootstrapPassword`
initializes `akadmin` only at the first install. It is not a password-rotation
mechanism. Keep `secretKey` unchanged across updates. The local users' passwords
and the client secret are reconciled from configuration; changing them rolls the
worker and proxy through secret-derived revision annotations.

Set `cookieSecret` to the URL-safe base64 encoding of exactly 32 random bytes
(44 characters including final `=`). For example, on a trusted execution host:

```sh
openssl rand -base64 32 | tr '+/' '-_' | pulumi config set --secret cookieSecret
pulumi preview --diff
pulumi up
```

Inspect the preview before applying it. RDS engine `17.7` and class
`db.t4g.micro` must be available in the selected subnets/region. The stack creates
no public ingress, load balancer, NodePort or public database.
`serverReplicas` and `workerReplicas` default to 1 and accept 1 through 4.
The sample has one replica and is not a production web framework.

## Use the protected portal

On the browser machine, `authentik.localhost` must resolve to `127.0.0.1`.
Chromium treats `.localhost` names as loopback. If your resolver does not,
configure that one hosts entry through your normal local administration process.
Do not point it at a public address.

The issuer hostname must be identical from both browser and proxy. Pulumi inserts
the dedicated issuer Service IP into the sample pod's hostAliases. This avoids
disabling issuer validation or substituting an internal hostname in tokens.

In separate terminals, keep these attended forwards running:

```sh
NS="$(pulumi stack output namespace)"
kubectl -n "$NS" port-forward --address 127.0.0.1 svc/authentik-loopback 9000:9000
```

```sh
NS="$(pulumi stack output namespace)"
kubectl -n "$NS" port-forward --address 127.0.0.1 svc/onboarding-sample 4180:4180
```

1. Open `http://localhost:4180/` in a fresh browser profile. Sign in as `alice`
   using the configured Alice password. Expect the first-day checklist and
   `alice@onboarding.example.test`.
2. Use a second, fresh profile with no existing IdP or application cookies.
   Sign in as `bob` with the valid Bob password. Expect Authentik's access-denied
   page; Bob is authenticated but lacks the application's allowed group.
3. If needed, visit `http://authentik.localhost:9000/if/admin/` as `akadmin` to
   inspect the application, provider, group binding and successful blueprint.
   Do not copy Secrets, token responses or raw browser storage into evidence.

The email scope marks only these two administratively provisioned demo identities
as verified; there is no enrollment or email-verification service here. Never
reuse that scope mapping for arbitrary or self-enrolled production identities.
The proxy also restricts the group and the reserved demo email domain.

Clearing the sample session does not clear the Authentik SSO session. Use a clean
profile for the denied-user check, or explicitly log out of both applications.
Sessions last five minutes; removing a group does not instantly revoke an existing
proxy cookie. This example makes no immediate session revocation promise.

## Private demo versus production

HTTP endpoints and non-Secure proxy cookies are restricted to this attended
loopback demonstration. Cluster networking is part of the trusted platform.
Production requires HTTPS at a stable DNS origin accessible from browser and
proxy, secure cookies, matching OIDC redirect/issuer and S3 CORS origins, reviewed
network controls, MFA, backup/restore operations and capacity planning. This
example does not implement or validate that production transition.

Kubernetes Secrets and encrypted Pulumi config keep secrets out of the source,
but state, Helm records, pod environment access and admin APIs are sensitive.
Do not export state or secret-bearing responses into Git. No bootstrap token is
created; application pods receive no AWS provisioning identity.

## Troubleshooting and costs

If the sample is not ready, inspect sanitized worker logs and the blueprint's
status first. Discovery cannot succeed until the blueprint has created the
application. Check `authentik.localhost` in the sample pod's hosts file and the
issuer Service endpoints. Do not bypass issuer, nonce or certificate validation.
Check private routing, the actual node security group and RDS TLS on database
errors. On IRSA failures verify the existing IAM OIDC provider, exact service
account subject and STS connectivity. No static S3 credentials should be added.

Paid resources include one RDS instance, at least 20 GiB database storage,
backups, S3 objects/versions/requests and the pre-existing EKS/node/network
foundation. Check current regional AWS pricing and reconcile the shared budget
before deploying. This source does not claim a fixed monthly price or free tier.

## Cleanup

Stop both port-forwards. Data protection is enabled by default: RDS and the S3
bucket are Pulumi-protected; RDS also has AWS deletion protection. An ordinary
destroy therefore refuses to delete persistent data.

For a known disposable trial, explicitly set `disposable=true` **before initial
deployment**. This disables those protections, skips the final RDS snapshot and
allows deletion of S3 versions; then `pulumi destroy` removes owned resources.
It never deletes the existing EKS/VPC/subnets/OIDC provider.

For retained data, take and verify the backups you require, empty the versioned
bucket only when authorized, turn off RDS deletion protection and remove Pulumi
protection deliberately. Keep `disposable=false` and choose a unique
`finalSnapshotIdentifier` if retaining a final RDS snapshot. Final snapshots incur
charges until explicitly deleted. This example has not tested backup restoration.
After cleanup, independently inventory owned RDS instances,
snapshots/backups, S3 versions, roles/policies and namespace resources.

## Attribution and support

Upstream licenses are preserved under `licenses/`. Authentik core is MIT with
separate enterprise terms; its Helm chart is GPL-3.0, including the unmodified
chart source bundled here. Bundled Bitnami/common chart code is Apache-2.0 and
disabled. oauth2-proxy is MIT. CPython uses PSF terms and Docker Python packaging
is MIT.

This example's authored code is licensed under the [MIT License](LICENSE) and
is provided as-is, without warranty or a support commitment. Third-party
components retain their own licenses and notices.

Private reviewers can use this repository's issues for example defects and submit
pull requests with a description of the change and its impact. Never include
credentials, state or private logs. Report sensitive findings privately to the
repository administrators through your organization's approved security channel.
Product support remains with the upstream projects.

Official references:

- https://docs.goauthentik.io/install-config/install/kubernetes/
- https://github.com/goauthentik/helm/tree/authentik-2026.8.5
- https://github.com/goauthentik/authentik/tree/version/2026.8.5
- https://docs.goauthentik.io/customize/blueprints/
- https://docs.goauthentik.io/add-secure-apps/providers/oauth2/
- https://docs.goauthentik.io/sys-mgmt/ops/storage-s3/
- https://github.com/oauth2-proxy/oauth2-proxy/tree/v7.15.6
- https://www.pulumi.com/registry/packages/kubernetes/api-docs/helm/v3/release/
