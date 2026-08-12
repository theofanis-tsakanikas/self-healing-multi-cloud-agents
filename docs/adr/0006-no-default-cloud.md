# ADR-0006: No default cloud, anywhere

- **Status:** Accepted
- **Date:** 2026-06 (recorded 2026-08)

## Context

Multi-cloud systems are usually built on one cloud and then ported. The first cloud becomes the
default: it is what the code assumes when a value is missing, what the tests exercise, and what
everyone develops against. The others get "support", which means they work until something changes.

The tell is small and everywhere — `region = os.getenv("AWS_REGION", "eu-central-1")`, a hardcoded
`s3://` in a helper, a validator that only knows one storage scheme. Each is harmless alone; together
they mean the second cloud is a port and the third is a rewrite.

## Decision

**AWS, GCP and Azure are equals**, and the provider is **always** read from `cloud_provider` in
config — never assumed, never defaulted. Concretely:

- Generated pipeline scripts keep **all three** `if _CLOUD == "aws"` / `elif "gcp"` / `elif "azure"`
  branches with real bodies, in the cloud-SDK import, the idempotency check and the credentials block.
  Only the active branch runs. Collapsing to one branch was tried, and it is precisely where the model
  intermittently dropped the SDK import or flattened the guard — the full skeleton is the
  proven-reliable form.
- Credentials in generated object-storage pipelines may be read **only** through `cloud_get()`;
  `os.getenv()` for the DB is a policy violation the validator catches. Resolution differs per cloud
  (AWS is three-tier through SSM; GCP and Azure read env vars) and the resolver hides that.
- Regions are never literals in generated workflows — always `${{ vars.AWS_DEFAULT_REGION }}`.

## Alternatives rejected

- **One primary cloud with support for the others.** Honest about effort, dishonest about capability,
  and it decays the moment the primary changes.
- **An abstraction layer over the three SDKs.** Would hide exactly the per-cloud differences worth
  demonstrating — an abfss netloc is `container@account.dfs.core.windows.net` while `s3://` and `gs://`
  put the bucket directly in the netloc, and pretending otherwise produces an HTTP 400 nobody can trace.
- **Collapsing generated scripts to the active cloud.** Tried, and reverted: it introduced the variance
  it was meant to remove.

## Consequences

- The same agent, the same config shape and the same self-healing loop drive three infrastructure
  APIs, and the identical routing fix healed an invalid Terraform value on all three.
- Generated scripts carry two unused branches. That is deliberate, and cheap.
- Every rule stated as "all clouds" needs checking against
  [ADR-0007](0007-databricks-as-a-distinct-execution-model.md): it usually means the three
  object-storage clouds, not the fourth provider.
