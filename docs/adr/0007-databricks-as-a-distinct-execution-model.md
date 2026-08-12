# ADR-0007: Databricks is a fourth provider with its own execution model

- **Status:** Accepted
- **Date:** 2026-06 (recorded 2026-08)

## Context

AWS, GCP and Azure share one execution model here: pandas reads the source database, writes Parquet to
object storage, Trino queries it, Grafana visualises it, all on Kubernetes. Adding Databricks as a
fourth *cloud* under that model would mean Spark pretending to be pandas and Delta pretending to be
Parquet on a bucket — which proves the abstraction, not the architecture.

But if the fourth provider needs its own path, the risk is the opposite: a special case that forks the
agent, so "cloud-agnostic" quietly means "agnostic across three".

## Decision

Databricks is a **fourth provider with a genuinely different execution model**, selected by the *same*
`provider:` switch and driven by its *own* standards (`databricks_spark_standard.md`,
`terraform_databricks.md`).

| | Object-storage clouds | Databricks |
|---|---|---|
| Compute | pandas on a Kubernetes Job | Spark on a jobs cluster |
| Storage | Parquet on S3 / GCS / ADLS | Delta in Unity Catalog |
| Query | Trino | Unity Catalog |
| Observability | Grafana + Prometheus Pushgateway | Lakeview dashboard over a per-run `_audit` Delta table |
| Credentials | `cloud_get()` | `dbutils.secrets.get` (password); host/name/user as job parameters |
| Artifacts | Dockerfile, `requirements.txt`, six K8s manifests | Spark script, UC DDL, Lakeview JSON, five Terraform files — **none** of the above |

What stays shared is what matters: the same Supervisor, the same evidence gate, the same patch
mechanism, the same bounded loop. The self-healing path is **one code path**, and the Databricks run
healed a runtime failure — a Spark job failing *on the cluster* over a wrong secret key — through it.

## Alternatives rejected

- **Databricks under the object-storage model.** Spark writing Parquet to a bucket and querying it
  through Trino is Databricks in name only, and would have demonstrated nothing about Unity Catalog.
- **A separate agent for Databricks.** Would fork the self-healing loop, which is the part worth
  generalising.
- **Leaving Databricks out.** The three object-storage clouds differ by vendor API. Proving the
  architecture generalises across a genuinely different *platform* is a stronger claim than proving it
  generalises across three variants of the same one.

## Consequences

- The `provider:` switch selects an execution model, not just a set of API endpoints.
- Every rule phrased as "all clouds" must say which it means — the three, or all four. `CLAUDE.md`
  calls this out explicitly for exactly this reason.
- The teardown is two-phase on Databricks alone: runtime-created managed tables need `force_destroy`
  applied into state before `terraform destroy`, so a plain destroy always fails.
- Natural-language authoring covers the three object-storage clouds only; the Databricks demo is a
  fixed config, and its source is fixed, so a free-text builder would add nothing.
