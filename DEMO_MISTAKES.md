# ⚠️ DEMO MISTAKE — TEMPORARY (revert after the self-healing recording)

ONE deliberate mistake injected into `knowledge_base/engineering/python_standards.md` to
demonstrate the Medic's **generation-phase** self-healing in a single GCP run
(`global_marketing`). **Revert it after recording.**

> The earlier two-mistake setup (`.astype(float)` + `.astype(int)`) was retired: it was
> sales-shaped (`unit_price` / `quantity` / `count` columns) and did **not** fire on the
> marketing dataset (`campaign_id, platform_name, ad_spend, clicks, impressions,
> event_timestamp`) — no quantity/count column → the `.astype(int)` loop was inert, and the
> architect ignored the buggy `.astype(float)` worked-example because the **rule** on line 109
> still said "NEVER `.astype(float)`". Both have been reverted to correct. The CI-runtime heal
> (`.astype(int)` → `IntCastingNaNError`) is **deferred to the Databricks recording**.

## The mistake — generation-time heal (validator catches it, never deploys)
- **File:** `knowledge_base/engineering/python_standards.md`
- **Line 109 (the RULE — this is the lever that makes it fire):**
  `**Numeric comparison columns — coerce, NEVER .astype(float):** ... Coerce with pd.to_numeric(chunk[col], errors='coerce') ... NEVER chunk[col].astype(float) ...`
  → `**Numeric comparison columns — cast with .astype(float):** ... Cast it with chunk[col] = chunk[col].astype(float) ...`
- **Line 111:** removed the trailing `Exactly like pd.to_numeric.` reference (so the date rule
  no longer points the architect back at `pd.to_numeric`).
- **Line 116 (worked example):** kept as `chunk['unit_price'] = chunk['unit_price'].astype(float)`
  (consistent with the flipped rule).
- **Effect:** the architect now follows the rule and emits `.astype(float)` on a numeric column →
  `validate_generated_code` flags it ("BUSINESS RULES: `.astype(float)` raises ValueError … use
  `pd.to_numeric(chunk[col], errors='coerce')` instead") → supervisor → **Medic** →
  `request_fix(architect)` → architect fix-mode patch → CLEAN. Caught at GENERATION — never
  deploys. After the heal the run pushes once and the CI deploy is green (no further mistakes).

## Revert (restore the correct standard)
```bash
# Line 109 — restore the rule:
#   **Numeric comparison columns — coerce, NEVER `.astype(float)`:** a column compared numerically
#   (`> 0`, `>= 0`, ranges) may carry dirty values (`'not_a_number'`, empty). Coerce with
#   `pd.to_numeric(chunk[col], errors='coerce')` (assign back) **before** comparing — dirty → `NaN`
#   → dropped as a rejected row; survivors are real numeric for the typed write. **NEVER
#   `chunk[col].astype(float)`** — it raises `ValueError` on the first bad value and crashes the
#   whole pipeline. (`.astype('Int64')` for the final integer cast is separate and still required — see Storage.)
# Line 111 — re-add ` Exactly like `pd.to_numeric`.` after the `NaT` sentence.
# Line 116 — restore: chunk['unit_price'] = pd.to_numeric(chunk['unit_price'], errors='coerce')
make ingest            # re-sync the CLEAN standard to Pinecone
rm DEMO_MISTAKES.md
```
(Lines 179 / 353 are ALREADY correct — `.astype('Int64')` — leave them.)

## To ARM the demo (before the run)
1. `make ingest` — push the tweaked standard to Pinecone (the architect reads standards from there).
2. Run the GCP pipeline (`global_marketing`) via Streamlit (agent runs locally, free) OR
   `run_agent.yml` with `sync_knowledge_base: NO` (so the committed-clean state, if any, does NOT
   overwrite the armed Pinecone tweak — though the tweak is committed here, so `sync` is also safe).
3. Watch: architect emits `.astype(float)` → validator FAILED → **Medic → request_fix → patch →
   CLEAN** → push → CI green. One heal, at generation.
