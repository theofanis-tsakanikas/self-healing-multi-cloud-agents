# ⚠️ DEMO MISTAKE — TEMPORARY (revert after the Databricks self-healing recording)

ONE deliberate mistake to demonstrate the Medic's **CI-runtime, INFRA-level** self-heal on the
Databricks `sales_lakehouse` pipeline — the hardest heal so far (the fix is in the Terraform, not
the script). **Revert it after recording.**

## The mistake (Databricks-only — isolated)
- **File:** `knowledge_base/infrastructure/terraform_databricks.md` (the `databricks_job` example).
- **Change:** REMOVED the JDBC driver `library { maven { coordinates = "org.postgresql:postgresql:42.7.3" } }`
  block (and its explanatory comment) from the `task {}` of `databricks_job.pipeline`.
- **Isolation:** this standard is retrieved ONLY for `provider: databricks` → AWS/Azure/GCP are
  unaffected.

## The flow (CI-runtime → INFRA heal)
1. Infra generates the `databricks_job` **without** the JDBC driver library (follows the standard).
2. Passes the validator (it's valid HCL) → deploy → `jobs run-now`.
3. Spark `spark.read.format("jdbc")` → **`ClassNotFoundException: org.postgresql.Driver`** at RUNTIME.
4. Medic verification fetches the CI logs → the NEW `_ci_error_owner` signature router (see below)
   sees `ClassNotFoundException` → routes the fix **deterministically to INFRA** (not architect) and
   points `healing_context` at the Terraform `library` block.
5. Infra `patch_project_file` adds the library → re-push (Scenario B) → re-deploy → `jobs run-now`
   → **green**.

## PERMANENT changes made alongside (do NOT revert these)
- `agents/medic.py`: `_CI_INFRA_SIGNATURES` + `_ci_error_owner()` — a CI-runtime error-signature →
  owner router (additive: only fires on JVM/Databricks signatures that never appear in the
  object-storage clouds' pandas tracebacks). In the CI-LOG branch, an infra signature now sets
  `deterministic_fix_target = "infra"` and a Terraform-targeted `healing_context`.
- `tests/test_medic_ci_infra_routing.py` — pins infra routing for ClassNotFoundException/library
  errors AND that pandas KeyError/ValueError/AnalysisException still route to the architect
  (zero mis-routing for the 4 validated clouds). 268 tests pass, ruff clean.

## Revert (restore the correct standard) — put back into the `task {}` (after `existing_cluster_id`):
```hcl
    # The cluster ships NO source-DB JDBC driver — attach it, or spark.read.format("jdbc")
    # fails ClassNotFoundException. Postgres source → postgresql; MySQL source → mysql-connector-j.
    library {
      maven { coordinates = "org.postgresql:postgresql:42.7.3" }
    }
```
Then: `make ingest` (verify `✅ Ingested: terraform_databricks.md`), `rm DEMO_MISTAKES.md`.

## ARM (before the run)
`make ingest` already done → the infra agent reads the standard from Pinecone. Run `sales_lakehouse`
(Streamlit Databricks deploy or run_agent with `sync_knowledge_base: NO`). Watch: deploy → job fails
ClassNotFoundException → Medic → **infra** patch (Terraform library) → re-deploy → green.
