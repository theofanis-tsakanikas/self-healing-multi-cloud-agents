# ⚠️ DEMO MISTAKES — TEMPORARY (revert after the self-healing recording)

Two deliberate, minimal mistakes injected into `knowledge_base/engineering/python_standards.md`
to demonstrate the Medic's self-healing in ONE GCP run. **Revert both after recording.**

## Mistake #1 — generation-time heal (validator catches it)
- **File:** `knowledge_base/engineering/python_standards.md` (worked example, ~line 116)
- **Change:** `chunk['unit_price'] = pd.to_numeric(chunk['unit_price'], errors='coerce')`
  → `chunk['unit_price'] = chunk['unit_price'].astype(float)`
- **Effect:** architect emits `.astype(float)` → `validate_generated_code` flags it
  ("BUSINESS RULES: `.astype(float)` raises ValueError…") → supervisor → **Medic** →
  `request_fix(architect)` → architect fix-mode patch → CLEAN. Caught at GENERATION — never deploys.

## Mistake #2 — post-push CI-runtime heal (passes the validator, crashes at runtime)
- **File:** `knowledge_base/engineering/python_standards.md` (~lines 179 and 353, both occurrences)
- **Change:** `chunk[col] = chunk[col].astype('Int64')` → `chunk[col] = chunk[col].astype(int)`
- **Effect:** architect emits `.astype(int)` → passes the validator (only `.astype(float)` is flagged)
  → deploys → at runtime `.astype(int)` on a NaN (float64 NULLable col) raises `IntCastingNaNError`
  in the pipeline pod → CI deploy fails → Medic `fetch_github_action_logs` sees the traceback
  (`File ".../scripts/pipe_*.py"`) → `request_fix(architect)` → patch → **re-push (NO terraform,
  Scenario B)** → re-deploy → green.

## Revert
```bash
git checkout -- knowledge_base/engineering/python_standards.md   # if uncommitted
# OR manually:
#   astype(float)      → pd.to_numeric(chunk['unit_price'], errors='coerce')
#   astype(int)        → astype('Int64')   (both occurrences)
make ingest            # re-sync the CLEAN standard to Pinecone
rm DEMO_MISTAKES.md
```

## To ARM the demo (before the run)
1. `make ingest`  — push the tweaked standard to Pinecone (the architect reads standards from there).
2. Run the GCP pipeline (Streamlit deploy or run_agent with `sync_knowledge_base: NO` so the local
   Pinecone tweak is NOT overwritten by the committed clean standard).
3. Watch: generation heal (#1) → push → CI fails → CI heal (#2) → verified.
