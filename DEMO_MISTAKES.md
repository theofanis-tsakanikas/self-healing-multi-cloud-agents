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

## PERMANENT changes alongside (do NOT revert) — real gaps the first demo run EXPOSED
- `agents/medic.py`: `_CI_INFRA_SIGNATURES` now also matches `secret does not exist` /
  `resource_does_not_exist` → infra (mirrors the ClassNotFoundException routing). The infra
  healing_context covers both library and secret fixes.
- `tests/test_medic_ci_infra_routing.py` — secret-not-found → infra; pandas/Spark script errors → architect.
- **`agents/medic.py` — EXACT secret-key fix (the 3rd run exposed this).** Even with the file content
  injected, the infra agent kept mis-targeting: the error names the SCOPE prominently ("scope: X and
  key: db_password"), so the LLM patched the `scope`/`name` (or a `<pipeline_id>` placeholder) and
  never the actual `key = "postgres_password"` line → no-op on the key → the heal looped to escalation
  (the earlier success was luck — 1 of 3 attempts happened to hit the key). `_databricks_secret_key_
  exact_fix` now READS the current `databricks_secret` `key = "<current>"` line from terraform/main.tf
  and puts the EXACT one-line `old`/`new` into the healing_context, so the agent copies it verbatim
  instead of guessing. `tests/test_medic_ci_infra_routing.py`.
- **`agents/codegen.py` + `cicd_standards.md` (the Databricks deploy workflow) — VISIBILITY fix.**
  The first run failed to heal because the REAL error never reached the Medic: the GHA log only
  had the generic "Workload failed, see run output for details", not "Secret does not exist". Two
  bugs: (a) `databricks jobs run-now` now WAITS by default → on failure exits with empty stdout →
  `RUN_ID=""` → poll loop dies with "invalid RUN_ID" (fixed with `--no-wait`); (b) on failure the
  workflow printed only the job state, not the task output (fixed — now calls
  `databricks jobs get-run-output <task_run_id>` and prints `.error`/`.error_trace`). Golden test
  updated. Without this, NO Databricks runtime failure is diagnosable.
- **`agents/infra.py` — RE-APPLY fix.** A Databricks infra heal was `patch_project_file` +
  `push_to_github` only, but the deploy workflow does NOT run `terraform apply` → the LIVE secret
  scope stayed stale and the job failed identically. Now the Databricks push-phase heal also binds
  `execute_terraform` (gated to `is_databricks` — object-storage clouds unchanged) and the fix
  prompt instructs patch → `execute_terraform apply` → push. `tests/test_infra_heal_routing.py`.
- **`agents/infra.py` — BLIND-PATCH fix (the 2nd run exposed this).** The infra agent never saw the
  target file's current content (it was trimmed from context many turns earlier, and `read_file`
  isn't even an infra tool), so it patched against the STANDARD's placeholders — it tried
  `old="<pipeline_id>"` (a skeleton placeholder absent from the real file) → `Applied: (none)`
  no-op → the heal looped on the unchanged error to escalation. Fix: `_inject_current_file_contents`
  reads the on-disk content of every infra-owned file named in the healing_context and injects it
  into the fix prompt as the SOURCE OF TRUTH for the `old` string (the standard is only a template).
  Plus a defense-in-depth guard: a no-op patch (`Applied:\n  (none)`) is now flagged as an error
  instead of a silent "success". `tests/test_infra_heal_routing.py`.
- **`agents/infra.py` — DETERMINISTIC heal completion + push-block fix.** Two more reliability gaps:
  (a) the patch→`terraform apply`→push sequence was LLM-driven (gpt-4o-mini could stop after the
  patch). Now, after a CLEAN applied patch on a Databricks fix, Python FORCES `execute_terraform
  apply` then `push_to_github` (gated to `is_databricks` fix mode; object-storage clouds unchanged).
  (b) latent bug never exercised by the validated runs (they pass first-try, no infra heal-push):
  in a heal `github_success` is True from the initial deploy, so the push was blocked as "already
  succeeded" → the fix never re-deployed. Now a prior-turn `github_success` no longer blocks a
  fix-mode push (only a duplicate push WITHIN the turn does). `tests/test_infra_heal_routing.py`.

## Revert
- `terraform_databricks.md`: `key = "postgres_password"` → `key = "db_password"`.
- `make ingest` (verify `✅ Ingested: terraform_databricks.md`), `rm DEMO_MISTAKES.md`.

## ARM (before the run)
`make ingest` done. Run `sales_lakehouse`. **Pre-warm the bootstrap jobs cluster** first
(`databricks clusters start 0618-082642-fdw5tmh8` or the Compute UI) so the job fails fast without a
5-min cold-start. Watch: deploy → job fails (Secret does not exist) → Medic → **infra** patch
(Terraform secret key) → re-deploy → green.
