# Runbook — Verifying a Pipeline Run & Operations

Operational verification steps and known failure signatures per platform. Dates refer to the validated end-to-end baselines.

---

## AWS (`eu_sales` baseline)

AWS CLI + kubectl run locally against the cluster using credentials from `.env` (load with `python-dotenv`; `aws eks update-kubeconfig --name multi-cloud-agent-cluster`). For in-cluster checks the CI job logs work too.

1. **S3:** `aws s3 ls s3://<bucket>/processed/ --recursive` → expect `run_date=YYYY-MM-DD/part_0.parquet`.
2. **Glue:** Table `<schema>.<pipeline_id>` exists with correct schema + the `run_date` partition registered (Trino `sync_partition_metadata`).
3. **Grafana:** LoadBalancer DNS on `:3000`, login `admin` / the `grafana-admin` K8s secret (`kubectl get secret grafana-admin -n monitoring -o jsonpath='{.data.admin-password}' | base64 -d`; pre-hardening deployments: `admin/admin`), dashboard "…Observability" — **5 panels** populated (Record Count, Last Success, Rejection Rate, Run Duration, Rejections by Reason). The 5th (piechart) shows the per-rule breakdown — empty/"No data" only when the pipeline has no DROP_RECORD/EXCLUDE_AND_LOG rules.
4. **Metrics:** all five gauges present in Prometheus — `pipeline_rows_processed_total`, `pipeline_last_success_timestamp`, `pipeline_rows_rejected_total`, `pipeline_duration_seconds`, `pipeline_rows_rejected_by_reason` (labeled per `reason`, one series per business rule).
5. **Cost note:** EKS + node EC2 + ECR persist after a run. `cleanup_k8s.yml` (manual `workflow_dispatch`) tears down only the K8s workloads — it never touches bootstrap infra.

### Grafana shows "No data" but the run succeeded?

Check in this order:
1. **Metrics actually in Prometheus?** `query pipeline_rows_processed_total` — if present, the pipeline + Pushgateway are fine and the problem is the dashboard.
2. **Dashboard query mismatch** — a stale/degraded `grafana-dash-config` ConfigMap on the cluster (hardcoded `project_id="unknown"`, no `$project_id` template var) never matches the real label → No data. The deployed ConfigMap can lag the committed (correct) artifact; re-applying the good `k8s/configmaps.yaml` + `kubectl rollout restart deploy/grafana` fixes it.
3. **Idempotency skip** — if `run_date=YYYY-MM-DD` already exists in storage the pipeline logs "already populated. Skipping" and returns *before* emitting metrics; the Pushgateway is also in-memory (a restart drops all metrics). Delete only that day's partition and re-run, or wait for the next day.

---

## Azure (`us_crm` baseline — validated e2e 2026-06-04)

- **Kubeconfig:** `az aks get-credentials -g multi-cloud-agent-rg -n multi-cloud-agent-aks`
- **ADLS:** `az storage fs file list -f <container> --account-name <acct> --path processed`
- **Trino:** `kubectl exec deploy/trino -- trino --execute "SELECT count(*) FROM hive.<schema>.<table>"`

---

## GCP (`global_marketing` baseline — validated e2e 2026-06-04→06)

- **Auth:** `gcloud container clusters get-credentials multi-cloud-agent-gke --region europe-west3` (needs `gke-gcloud-auth-plugin` locally — install via `gcloud components install`), or query **Cloud Logging** instead: `gcloud logging read 'resource.labels.container_name="pipeline"'`.
- **GCS:** `gcloud storage ls gs://<bucket>/processed/` → `run_date=YYYY-MM-DD/part_0.parquet`.
- **Trino:** `kubectl exec deploy/trino -- trino --execute "SELECT count(*) FROM hive.<schema>.<table>"`.
- **Grafana:** the LoadBalancer external IP from `gcloud compute forwarding-rules list` (port 3000), login `admin` / the `grafana-admin` secret (pre-hardening deployments: admin/admin).
- **Required repo Secrets:** `GCP_SA_KEY_JSON`, `MYSQL_DB_PASSWORD`; **Variables:** `GCP_PROJECT_ID`, `MYSQL_DB_HOST/PORT/USER/NAME`.

GCP-specific deploy invariants:
- The **job.yaml image uses `:latest`** (the build pushes it; **NO image-tag sed** — see the CI/CD standard).
- The **`processed/` directory is pre-created** by the pipeline terraform (`google_storage_bucket_object`), else Trino `CREATE TABLE` fails "External location must be a directory".
- The terraform **backend prefix = `CLOUD_SETUP.state_prefix` verbatim** — a self-derived prefix splits state → `409 bucket already exists`.

---

## Databricks (`sales_lakehouse` baseline — validated e2e 2026-06-08)

Local Databricks CLI with `DATABRICKS_HOST` / `DATABRICKS_TOKEN` sourced from `.env`; the source RDS must be `available` first (`aws rds start-db-instance --db-instance-identifier sales-lakehouse-raw-data`).

- **Run state:** `databricks jobs list-runs --job-id <id>` → `result=SUCCESS`.
- **Data write (from the script's own logs):** `databricks jobs get-run-output <task_run_id>` → grep `Wrote N rows` + `Audit row written` (the baseline: 100 seeded chaos rows → 68 written / 32 rejected, `monetary_integrity`+`temporal_validity`).
- **Delta / Unity Catalog:** table `multi_cloud_agent_workspace.raw.pipe_sales_lakehouse` + its `_audit` table.
- **Dashboard (the Grafana equivalent):** a Lakeview dashboard "`<pipeline> — Observability`" under **/Shared** (workspace → Dashboards) — 5 metrics off the `_audit` table (records processed/rejected, rejection rate, run duration, rejections-by-reason bar) on the serverless SQL warehouse; `terraform output -raw dashboard_id`.

### Known failure signatures

- **Widgets all showing `[INSUFFICIENT_PERMISSIONS] … does not have USE CATALOG`:** the dashboard runs its queries as the **human viewer** (`embed_credentials=false`), so the viewer needs Unity Catalog read — the databricks bootstrap grants the built-in `account users` group `USE_CATALOG`+`USE_SCHEMA`+`SELECT` on the catalog (`databricks_grants.catalog_read`). If missing, re-run the databricks bootstrap. (UC access is explicit — even admins need it. This is NOT a data/idempotency problem — the `_audit` rows are there; the widgets just can't read the catalog.)
- **Spark UI: a stage stuck at `0/1` with duration "Unknown" = ZERO executors** — the cluster was a broken single-node (`num_workers=0` + UC SINGLE_USER with no `spark.master=local[*]`); the fix is `num_workers=1` in `bootstrap/databricks/main.tf` (the read task never gets a slot otherwise).
- **A *running-but-stuck* task** instead points at the JDBC read — verify `?sslmode=require` + DBR runtime **18.2** (not 14.3, which hangs the SSL handshake).

### Cost (pause)

`aws rds stop-db-instance …` + `databricks clusters delete <id>` (config persists, auto-restarts next run); the serverless SQL warehouse auto-stops. **No `cleanup_k8s.yml`** applies (Databricks has no K8s).

### Full teardown

`destroy.yml` (manual `workflow_dispatch`, `cloud: databricks`) destroys the workspace + Unity Catalog + jobs cluster + serverless warehouse + source RDS + DBFS S3 bucket + IAM, keeping ONLY `s3://multi-cloud-agent-bootstrap-state` (the terraform backend — a backend, NOT a managed resource, so `terraform destroy` never touches it; same as every cloud's kept state bucket).

- **Phase 1** destroys the pipeline terraform (job + secret scope + `databricks_dashboard`).
- **Phase 2** a targeted `apply` writes the teardown flags into state — `force_destroy` on `dbfs_root`/metastore/catalog/`raw` schema/storage credential/external location, **plus `force_update`** on the storage credential & external location — then `terraform destroy`.

All need the flags because the Spark job creates **managed tables at runtime** (not in terraform), so a plain destroy fails sequentially: schema *'not empty'* → external location *'N dependent managed tables'* → credential *'use force option to update'*.

The job env MUST pass `TF_VAR_databricks_client_id` (the SP app id — a required, no-default bootstrap+pipeline var; without it both phases abort "No value for required variable") alongside the SP (`DATABRICKS_CLIENT_ID`/`SECRET`/`ACCOUNT_ID`) + AWS creds.
