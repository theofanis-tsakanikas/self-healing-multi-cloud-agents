# Security

This document describes the project's security posture — what is hardened, what is a **deliberate demo trade-off**, and what the production-grade alternative would be. The project provisions real cloud infrastructure for end-to-end validation; several choices optimize for demo cost and operability and are documented here rather than silently shipped.

## Reporting a vulnerability

Open a GitHub issue or contact the maintainer directly (see the repo profile). This is a portfolio/demo project — there is no bug-bounty program, but reports are welcome and acted on.

---

## Hardened by design

| Area | Posture |
|---|---|
| **Secrets in git** | No credentials are committed — verified across the full git history. `.env`, `.env.bootstrap`, `.bootstrap_outputs.json` (contains account/subscription/project IDs) are gitignored; `.env.example` is the placeholder-only reference. |
| **Credential access in generated code** | Generated pipeline scripts may only read DB credentials through `cloud_get()` (AWS: SSM via IRSA; Azure/GCP: K8s-secret-injected env). `os.getenv()` for DB variables is a policy violation rejected by `validate_generated_code`. Databricks scripts use `dbutils.secrets.get` against a secret scope fed from SSM. |
| **CI secret/variable split** | Credentials live in GitHub **Secrets**; non-sensitive config (regions, DB hosts/users) in GitHub **Variables**. Generated workflows never contain literals for either. |
| **Workload identity** | Pipeline pods authenticate to object storage via IRSA (AWS) / Workload Identity (GKE) / managed identity + scoped account key (AKS) — no long-lived cloud keys inside the cluster. |
| **Workflow token scope** | All repo workflows declare least-privilege `permissions: contents: read`. Deploys authenticate to the clouds with their own credentials, never with `GITHUB_TOKEN`. |
| **Grafana admin** | The deploy workflow creates a `grafana-admin` K8s Secret (from the optional `GRAFANA_ADMIN_PASSWORD` repo secret, or a fail-secure random fallback) and Grafana reads `GF_SECURITY_ADMIN_PASSWORD` from it — the default `admin/admin` is never shipped on the public LoadBalancer. Retrieve it with `kubectl get secret grafana-admin -n monitoring -o jsonpath='{.data.admin-password}' | base64 -d`. |
| **Action pinning** | Third-party GitHub Actions are pinned to release tags — never a mutable branch. |
| **Hermetic CI tests** | `tests.yml` runs with no cloud access and no credentials; every external dependency is mocked. |

---

## Deliberate demo trade-offs (and the production posture)

These are conscious decisions for a cost-bounded, single-operator demo environment. Each one is the first thing to change in a production deployment.

### 1. Source databases have public endpoints
All four source databases (AWS RDS, Azure PostgreSQL Flexible, GCP Cloud SQL, the Databricks source RDS) are provisioned with a public endpoint, restricted by CIDR allowlist / firewall rules (e.g. `TF_VAR_rds_allowed_cidrs`).

- **Why:** the agent, the chaos seeder, and GitHub-hosted CI runners all connect from outside the VPCs; private endpoints would require per-cloud VPN/peering plus self-hosted runners — significant fixed cost for a demo.
- **Production:** private endpoints (RDS in private subnets, Azure Private Link, Cloud SQL private IP), VPC peering or self-hosted runners inside the network, and IAM-based DB auth where supported.

### 2. Grafana is exposed on a public LoadBalancer
The dashboard is the demo's visible artifact, so it is intentionally reachable (password-protected — see above).

- **Production:** internal LB + ingress with SSO (OIDC), `loadBalancerSourceRanges`, or access via port-forward/VPN only.

### 3. Pod `securityContext` is not enforced in generated manifests
The stack images already run as non-root users (grafana `472`, trino `trino`, prometheus `nobody`, the pipeline image creates `appuser`), but the manifests do not pin `runAsNonRoot`/`runAsUser`.

- **Why:** several upstream images declare *non-numeric* users, so a blanket `runAsNonRoot: true` fails kubelet verification ("cannot verify user is non-root") unless `runAsUser` is pinned per image — a coordinated change across four manifest skeletons and the Dockerfile standard. Deferred deliberately rather than half-applied.
- **Production:** numeric UIDs in all images, pod-level `runAsNonRoot` + `runAsUser`, `readOnlyRootFilesystem` where the app allows, NetworkPolicies between namespaces, and Pod Security Standards (`restricted`) on the namespaces.

### 4. Trino has no authentication inside the cluster
Trino is ClusterIP-only (never exposed publicly) and queried in-cluster or via `kubectl port-forward`/`kubectl exec`.

- **Production:** Trino with TLS + password/OAuth authenticator and per-catalog access control.

### 5. `GH_PAT` is a classic personal access token
The agent pushes generated artifacts (including `.github/workflows/` files) and re-triggers CI. The built-in `GITHUB_TOKEN` cannot do this: it lacks the `workflow` scope (403 on workflow-file pushes) and its pushes intentionally do not re-trigger workflows. A classic PAT with `repo` + `workflow` is the minimal working credential.

- **Production:** a GitHub App installation token (finer-grained, auditable, auto-expiring) or a fine-grained PAT once it covers the `workflow`-push case for the org.

### 6. Prometheus Pushgateway is in-memory
Metrics are demo telemetry; a Pushgateway restart drops them (documented in the verification runbook).

- **Production:** durable metrics via remote-write to a managed TSDB (AMP, Azure Monitor, GCM) and alerting rules with paging.

### 7. The knowledge base is uploaded to Pinecone (third-party SaaS)
`knowledge_base/*.md` is embedded and stored in Pinecone (full documents, including their text in metadata) so the agents can retrieve standards at generation time. The corpus is audited to contain **no credentials** — but it does carry non-sensitive environment details (resource-naming conventions, the project's SSM namespace, backend/state conventions).

- **Why:** RAG over the org's own engineering standards is the core mechanism; the content is classification-level *internal*, not *secret*.
- **Production:** a self-hosted or VPC-peered vector store (e.g. pgvector, OpenSearch), or a Pinecone tier with CMEK/private networking; keep secrets out of standards as a hard rule either way (enforced here by gitleaks in CI and by the credential-access policy in generated code).
