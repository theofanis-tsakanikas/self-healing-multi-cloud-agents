# Policy-as-code gate over the agent's GENERATED infrastructure

The agent autonomously writes Dockerfiles, Kubernetes manifests and CI workflows and ships them to
production. This directory is a **security gate** over that generated bundle — a scoped, on-theme
control for an autonomous "responsible AI data engineer": it refuses infrastructure that would be
unsafe to deploy.

## Two engines, one set of rules

- **`../security_analyzer.py`** — the deterministic Python analyzer. It extracts a normalized security
  *context* (facts) from a generated-artifact directory and derives HIGH violations. **This is the
  source of truth and the enforced CI gate** (`make security-gate`, `tests/test_security_gate.py`).
- **[`generated_infra.rego`](generated_infra.rego)** — an **independent re-implementation** of the
  same HIGH rules in [Rego](https://www.openpolicyagent.org/) for Open Policy Agent / Conftest. It is
  a second opinion (defence in depth on the *rule logic*) and a **portability proof** — the same rules
  most platform teams already run, so they could be enforced at admission-control / gateway time.

### The HIGH rules

| Rule | Fires when |
|---|---|
| `DOCKERFILE_ROOT_USER` | a generated Dockerfile has no non-root `USER` |
| `DOCKERFILE_COPIES_ENV` | a Dockerfile `COPY`s a `.env` / secrets file |
| `K8S_INLINE_SECRET` | a container env carries a credential as an inline literal `value` (not `valueFrom`) |
| `IMAGE_PUBLIC_LATEST` | a **public** image is pinned to `:latest` (private registries push an immutable `:sha` alongside and are exempt) |
| `WORKFLOW_INLINE_SECRET` | a workflow hardcodes a secret literal instead of `${{ secrets.* }}` |

`POD_NOT_NONROOT` is reported as **ADVISORY** (never denies): enforcing `runAsNonRoot` needs per-image
numeric UIDs validated on a live cluster — see SECURITY.md, deliberate trade-off #3.

## Honest limit

Both engines read the **same** upstream fact extraction (`extract_context`), so a bug there would feed
both identically. This is a rule-logic cross-check, not two independent extraction pipelines — the
same caveat the governance-platform gate states.

## Run it

```bash
make security-gate    # Python gate over the live generated bundle (k8s/, Dockerfile, workflows)
make gate-proof       # prove the gate REFUSES the unsafe fixtures + PASSES the clean v1.0.0 goldens
make opa              # cross-check with the Rego policy (needs conftest; skipped offline)
```

When `conftest` is not installed, the Rego cross-check is skipped and the Python gate remains the
enforced control (`tests/test_security_gate.py` runs in the normal hermetic suite).
