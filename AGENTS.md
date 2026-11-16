# Authentik example scope

Keep Python Pulumi, the official pinned Authentik chart and upstream oauth2-proxy.
Target an existing EKS cluster in us-east-2. Maintain the blueprint and actual
allowed/denied identity checks. The sample backend must bind only pod loopback.
Keep issuer equality and nonce/signature checks enabled. Default to ClusterIP,
loopback-only port-forwards and protected persistent data.

Do not commit state, kubeconfig, passwords, tokens, secret-bearing logs or real
account identifiers. Do not create clusters, test runners or publication workflows.
Preserve the authored-code MIT License and all upstream licenses and notices.
Do not claim deployment, recovery or scaling validation without exact evidence.
Scan exact source, chart, dependency and container bytes before execution in an
approved sandbox. Never execute this workload or providers on the workstation.
