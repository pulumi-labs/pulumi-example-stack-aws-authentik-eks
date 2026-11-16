# Authentik OIDC onboarding on Amazon EKS with Python Pulumi

This example deploys a private sign-in service and a small "team onboarding"
web page that only approved people can open. It runs on a Kubernetes cluster
you already have in AWS region `us-east-2`, and Pulumi (infrastructure as code
written in Python) creates everything else.

## What you'll build

When you're done, you'll have:

- **Authentik**, an open source identity provider. It stores users and groups
  and handles sign-in for other apps.
- A **protected onboarding portal**. Users sign in through Authentik. Members
  of the `onboarding-members` group see a first-day checklist, and everyone
  else is turned away.
- Two demo users: `alice`, who is in the group, and `bob`, who isn't.

A few terms you'll see throughout:

- **EKS** (Amazon Elastic Kubernetes Service) is AWS's managed Kubernetes. This
  example runs inside an EKS cluster you already have. It doesn't create one.
- **RDS** (Amazon Relational Database Service) is AWS's managed database.
  Authentik stores its data in an RDS PostgreSQL database.
- **S3** is AWS object storage. Authentik keeps uploaded files there.
- **IRSA** (IAM Roles for Service Accounts) lets one Kubernetes pod borrow an
  AWS role through short-lived credentials, so nobody has to store AWS keys
  inside the cluster.
- **OIDC** (OpenID Connect) is the standard sign-in protocol that Authentik
  speaks. An app sends you to Authentik, you sign in there, and Authentik sends
  back a signed token that says who you are.
- **Helm** is the Kubernetes package manager. A Helm "chart" is a packaged app.
  This example installs the official Authentik chart.
- **oauth2-proxy** is a small open source gateway that sits in front of a web
  app and only lets signed-in, authorized users through.

## How it works

The official Authentik Helm chart runs one server and one worker. PostgreSQL
stores identities, keys, sessions and background coordination. This Authentik
release doesn't need Redis.

The RDS database is private and encrypted, keeps backups for seven days, and is
reached over verified TLS using the included regional AWS certificate bundle.
The S3 bucket is private, encrypted and versioned, with public access blocked.
Authentik reaches it through short-lived IRSA credentials.

Users, groups and the app are created by a **blueprint**, Authentik's built-in
way to declare objects in a YAML file. The worker loads `config/onboarding.yaml`
at normal startup. It creates `alice`, `bob`, `onboarding-members`, a
confidential OIDC client that only allows the authorization code flow, and an
application bound to that group. Only Alice is a member. Passwords and secrets
reach the worker through environment references, never as literal values in the
YAML. You don't need a bootstrap API token or any extra provisioning command.

The sample pod holds two containers: upstream oauth2-proxy and
`sample/portal.py`. The proxy does OIDC discovery, checks the token signature,
issuer, audience, nonce and state, uses PKCE S256, and checks group membership.
The small Python page then shows who you are and a first-day checklist. The
Python backend listens only on `127.0.0.1:8080` inside the pod, and only the
proxy's port 4180 has a Kubernetes Service.

### Why blueprints instead of a Terraform provider

Blueprints are an official Authentik feature. Using them avoids a long-lived
admin API token, a generated SDK, and a second "set up the API, then configure
it" phase. One thing to know: deleting a blueprint file doesn't delete the
objects it created. To remove a single object, use blueprint `state: absent`.
Destroying the whole stack removes the Authentik instance and everything in it.

## Before you start

You'll need these already in place:

1. **An EKS cluster** in `us-east-2` with Linux AMD64 nodes. The nodes need
   network access to private RDS subnets, public container registries, S3 and
   the regional AWS STS HTTPS API.
2. **An IAM OIDC provider for the cluster**, so IRSA works. The stack only
   creates its own workload role and service account. It doesn't change shared
   cluster setup.
3. **Two or more private subnets** in different availability zones in the same
   VPC as the cluster, plus the security group your nodes actually use. If your
   cluster uses security groups for pods, change the database rule to use the
   real pod security group before you deploy. Don't guess.
4. **Tools:** AWS CLI v2, CPython 3.12, uv, Pulumi CLI 3.273.0, and the Pulumi
   providers AWS 7.52.1 and Kubernetes 4.34.2. The dependency lock includes
   hashes for Linux ARM64 and Linux AMD64.
5. **Access:** AWS credentials scoped to create these resources, and permission
   to manage a namespace in the cluster.
6. **A browser machine** that can run `kubectl port-forward` to the cluster on
   its own loopback address.

You don't need Tailscale, a Kubernetes operator, a CSI driver, a custom CI
runner, or a new cluster.

**Safety first:** this deploys third-party software. Review and malware-scan the
exact source, chart, dependency and container artifacts before you run
anything, and run every command below in an isolated, disposable environment,
not on your everyday workstation.

## Step 1: Get the code

The repository is private, so clone it with an account that has access:

```sh
git clone https://github.com/pulumi-labs/pulumi-example-stack-aws-authentik-eks.git
cd pulumi-example-stack-aws-authentik-eks
```

## Step 2: Install dependencies and create a stack

From that directory, create a virtual environment from the hash-locked
dependencies, then create a Pulumi stack (one deployment of this project) and
point it at your cluster:

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

A few notes:

- `PRIVATE_SUBNET_IDS` is a comma-separated list of subnet IDs with no spaces.
- Use a lowercase stack name of 24 characters or fewer.
- Provide AWS credentials through `AWS_PROFILE` or scoped temporary credential
  environment variables. Never put credentials in this directory.
- The generated kubeconfig uses AWS CLI `eks get-token` with your cluster's
  endpoint and certificate authority.

### Optional settings

- `resourcePrefix` sets the namespace and the prefix for the IAM role,
  database, subnet group, security group and bucket names. It defaults to
  `authentik-<stack>` and accepts a lowercase DNS label of up to 40 characters.
- `permissionsBoundaryArn` attaches an IAM permissions boundary to the
  application role. It's not required for normal use.
- `serverReplicas` and `workerReplicas` default to 1 and accept 1 through 4.

Every AWS resource the stack owns that supports tags gets `pulumi-project` and
`pulumi-stack` tags.

## Step 3: Set the secrets

Generate each of these with a password manager and make every one different.
Each must be 32 to 128 characters made of URL-safe letters, digits, `_` or `-`.
Pulumi prompts you for each value and stores it encrypted:

```sh
pulumi config set --secret databasePassword
pulumi config set --secret secretKey
pulumi config set --secret bootstrapPassword
pulumi config set --secret alicePassword
pulumi config set --secret bobPassword
pulumi config set --secret clientSecret
```

What each one is for:

- `alicePassword` and `bobPassword`: keep these somewhere safe. You'll need
  them to sign in later.
- `bootstrapPassword`: sets the `akadmin` admin password **only on the first
  install**. Changing it later doesn't rotate the password.
- `secretKey`: Authentik's internal key. Don't change it after deploying.
- The user passwords and `clientSecret` are kept in sync with your config.
  Changing them restarts the worker and proxy automatically.

## Step 4: Preview and deploy

Set `cookieSecret` to the URL-safe base64 form of exactly 32 random bytes (44
characters, ending in `=`). On a trusted machine, this does it for you. Then
preview and deploy:

```sh
openssl rand -base64 32 | tr '+/' '-_' | pulumi config set --secret cookieSecret
pulumi preview --diff
pulumi up
```

Read the preview before you confirm. RDS engine `17.7` and instance class
`db.t4g.micro` must be available in your subnets and region. The stack creates
no public ingress, load balancer, NodePort or public database. The sample app
runs as a single replica and isn't a production web framework.

## Step 5: Open the portal

The browser and the proxy must see Authentik at exactly the same hostname,
`authentik.localhost`. On your browser machine it must resolve to `127.0.0.1`.
Chromium already treats `.localhost` names as loopback. If your system doesn't,
add that one hosts-file entry the way you normally would. Never point it at a
public address.

Inside the cluster, Pulumi adds the issuer Service IP to the sample pod's
`hostAliases`, so the proxy sees the same name. That way nobody has to turn off
issuer checks or swap in an internal hostname.

Open two terminals and leave these port-forwards running while you test:

```sh
NS="$(pulumi stack output namespace)"
kubectl -n "$NS" port-forward --address 127.0.0.1 svc/authentik-loopback 9000:9000
```

```sh
NS="$(pulumi stack output namespace)"
kubectl -n "$NS" port-forward --address 127.0.0.1 svc/onboarding-sample 4180:4180
```

## Step 6: Check that it works

1. Open `http://localhost:4180/` in a fresh browser profile and sign in as
   `alice` with Alice's password. **Success:** you see the first-day checklist
   and `alice@onboarding.example.test`.
2. Open a second fresh profile with no saved cookies. Sign in as `bob` with
   Bob's password. **Success:** Bob cannot open the protected checklist. He may
   reach his own Authentik settings after the denial; being signed in does not
   make him a member of the allowed group.
3. Optionally, open `http://authentik.localhost:9000/if/admin/` and sign in as
   `akadmin` to look at the application, provider, group binding and blueprint
   status. Don't copy secrets, token responses or raw browser storage into notes
   or tickets.

Good to know:

- The email scope marks only these two admin-created demo users as verified.
  There's no sign-up or email verification here. Don't reuse that mapping for
  real or self-registered users.
- The proxy also limits access to the group and the reserved demo email domain.
- Signing out of the portal doesn't sign you out of Authentik. Use a clean
  profile for Bob, or log out of both.
- Sessions last five minutes. Removing someone from the group doesn't kill a
  proxy session they already have, and this example doesn't promise instant
  revocation.

## Moving toward production

This example uses plain HTTP and non-Secure cookies, and that's only acceptable
for this loopback-only demo where the cluster network is trusted. Production
needs, at minimum: HTTPS on a stable DNS name reachable by both browser and
proxy, secure cookies, matching OIDC redirect, issuer and S3 CORS origins,
reviewed network controls, MFA, backup and restore procedures, and capacity
planning. This example doesn't implement or test any of that.

Encrypted Pulumi config and Kubernetes Secrets keep secrets out of the code,
but Pulumi state, Helm release records, pod environment access and admin APIs
are still sensitive. Never commit state or secret-bearing output to Git. No
bootstrap token is created, and the application pods get no AWS provisioning
permissions.

## Troubleshooting

- **Malformed sign-in callback:** the pinned oauth2-proxy can return HTTP 500
  for invalid callback state. The protected page stays inaccessible.

- **Sample pod not ready:** check the worker logs (with secrets removed) and
  the blueprint status first. OIDC discovery can't succeed until the blueprint
  has created the application. Then check `authentik.localhost` in the sample
  pod's hosts file and the issuer Service endpoints. Don't work around this by
  turning off issuer, nonce or certificate checks.
- **Database errors:** check private routing, the node security group you
  configured, and RDS TLS.
- **IRSA errors:** check the cluster's IAM OIDC provider, the exact service
  account subject, and STS connectivity. Don't add static S3 keys.

## Costs

You pay for one RDS instance, at least 20 GiB of database storage, backups, S3
objects, versions and requests, plus the EKS cluster, nodes and networking you
already run. Check current AWS pricing for your region and your own budget
before deploying. There's no fixed monthly price or free-tier claim here.

## Cleanup

Stop both port-forwards first.

Data protection is on by default. Pulumi protects the RDS database and the S3
bucket, and RDS also has AWS deletion protection turned on, so a plain
`pulumi destroy` refuses to delete your data.

**For a throwaway trial,** set `disposable=true` **before the first deploy**.
That turns off those protections, skips the final RDS snapshot and allows S3
versions to be deleted. After that, `pulumi destroy` removes everything the
stack owns. It never touches your existing EKS cluster, VPC, subnets or OIDC
provider.

**To keep your data,** take and verify the backups you need first. Empty the
versioned bucket only when you've decided to, turn off RDS deletion protection,
and remove Pulumi protection on purpose. Leave `disposable=false` and choose a
unique `finalSnapshotIdentifier` if you want a final RDS snapshot. Final
snapshots cost money until you delete them.

After cleanup, check by hand that no RDS instances, snapshots or backups, S3
object versions, IAM roles or policies, or namespace resources owned by this
stack are left.

## License and attribution

This example's authored code is licensed under the [MIT License](LICENSE) and
is provided as-is, without warranty or a support commitment. Third-party
components keep their own licenses and notices, which are preserved under
`licenses/`:

- Authentik core is MIT, with separate enterprise terms.
- The Authentik Helm chart is GPL-3.0, including the unmodified chart source
  bundled here.
- The bundled Bitnami common chart code is Apache-2.0 and disabled.
- oauth2-proxy is MIT.
- CPython uses the PSF license, and the Docker Python packaging is MIT.

## Support and contributing

This repository is private. If you have access, use its Issues to report
example defects, and send pull requests that explain what you changed and why.
Never include credentials, state or private logs. Report sensitive security
findings privately to the repository administrators through your
organization's security channel. Product support comes from the upstream
projects.

## Official references

- https://docs.goauthentik.io/install-config/install/kubernetes/
- https://github.com/goauthentik/helm/tree/authentik-2026.8.5
- https://github.com/goauthentik/authentik/tree/version/2026.8.5
- https://docs.goauthentik.io/customize/blueprints/
- https://docs.goauthentik.io/add-secure-apps/providers/oauth2/
- https://docs.goauthentik.io/sys-mgmt/ops/storage-s3/
- https://github.com/oauth2-proxy/oauth2-proxy/tree/v7.15.6
- https://www.pulumi.com/registry/packages/kubernetes/api-docs/helm/v3/release/
