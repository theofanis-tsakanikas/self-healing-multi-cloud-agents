# ⚠️ DEMO MISTAKE — TEMPORARY (revert after the Databricks self-healing recording)

ONE deliberate mistake to demonstrate the Medic's **CI-runtime, INFRA-level** self-heal on the
Databricks `sales_lakehouse` pipeline — the job FAILS at runtime, the Medic fixes the **Terraform**
(not the script), re-deploys, and the re-run goes green. **Revert it after recording.**

(Replaces the retired missing-JDBC-library demo, which was a no-op: the Databricks Runtime already
bundles the Postgres driver, so the job ran green. The secret-key mismatch below is reliable — the
DBR cannot "save" a secret that does not exist.)

## The mistake (Databricks-only — isolated)
- **File:** `knowledge_base/infrastructure/terraform_databricks.md`, the `databricks_secret` resource.
- **Change:** `key = "db_password"` → **`key = "postgres_password"`**.
- The Spark script (databricks_spark_standard.md) reads `dbutils.secrets.get(scope, "db_password")` —
  so the Terraform now creates the secret under a key the script does NOT request → mismatch.
- **Isolation:** this standard is retrieved ONLY for `provider: databricks` → AWS/Azure/GCP unaffected.

## The flow (CI-runtime → INFRA heal)
1. Infra generates the Terraform with the wrong secret key (follows the standard). Passes the
   validator (no secret-key cross-check) → deploy → `jobs run-now`.
2. The Spark job fails FAST at `dbutils.secrets.get(scope, "db_password")` →
   **`Secret does not exist with scope: … and key: db_password`** (before the JDBC read — cheap).
3. Medic verification fetches the CI logs → `_ci_error_owner` matches `secret does not exist` →
   routes the fix DETERMINISTICALLY to **infra** + points healing_context at the Terraform secret key.
4. Infra `patch_project_file` sets the `databricks_secret` key to `db_password` (the key the script
   reads — taken from the error/script, NOT the wrong standard) → re-push → re-deploy → green.

## Why this heals (and why the program generated it wrong first)
- It generated wrong because the STANDARD says the wrong key (the injected mistake). In a clean run
  the standard is consistent → correct key → works first time.
- The Medic does NOT guess the right key — it RECONCILES from runtime evidence: the error names the
  missing key (`db_password`) and the script reads `db_password`; the secret must match that. The
  script's `dbutils.secrets.get` key is the contract; the Terraform is aligned to it.

## PERMANENT changes alongside (do NOT revert)
- `agents/medic.py`: `_CI_INFRA_SIGNATURES` now also matches `secret does not exist` /
  `resource_does_not_exist` → infra (mirrors the ClassNotFoundException routing). The infra
  healing_context covers both library and secret fixes.
- `tests/test_medic_ci_infra_routing.py` — secret-not-found → infra; pandas/Spark script errors → architect.

## Revert
- `terraform_databricks.md`: `key = "postgres_password"` → `key = "db_password"`.
- `make ingest` (verify `✅ Ingested: terraform_databricks.md`), `rm DEMO_MISTAKES.md`.

## ARM (before the run)
`make ingest` done. Run `sales_lakehouse`. **Pre-warm the bootstrap jobs cluster** first
(`databricks clusters start 0618-082642-fdw5tmh8` or the Compute UI) so the job fails fast without a
5-min cold-start. Watch: deploy → job fails (Secret does not exist) → Medic → **infra** patch
(Terraform secret key) → re-deploy → green.
