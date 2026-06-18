# ⚠️ DEMO MISTAKE — TEMPORARY (revert after the GCP self-healing recording)

ONE deliberate mistake in `knowledge_base/engineering/python_standards.md` to demonstrate the
Medic's **generation-phase** self-heal in a single GCP run (`global_marketing`), shaped so the
heal produces a script that ALSO runs green at CI. **Revert it after recording.**

## The mistake — generation-time heal (validator catches it, deploys green after the fix)
- **File / line ~109 (the RULE):** instructs `.astype(float)` instead of `pd.to_numeric`, but
  keeps the correct **separate-statement** structure (cast on its own line, then `.fillna(0).clip(lower=0)`
  — NOT a chained `.where`). The worked example (line ~125) is `.astype(float)` for consistency.
- **Flow:** architect emits `chunk['ad_spend'] = chunk['ad_spend'].astype(float)` (own line) +
  `.fillna(0).clip(lower=0)` → `validate_generated_code` flags `.astype(float)` →
  supervisor → **Medic** → `request_fix(architect)` → patch `.astype(float)` → `pd.to_numeric(...)` →
  result is the clean **two-statement** form → CLEAN → push → **CI green** (verified on real data:
  ad_spend is MySQL `text`, coerce→fillna→clip gives min 0 / no nulls / no crash).

Why this shape (vs the earlier failed attempts): a chained `.where(chunk[col] >= 0)` on the
coerce reads the ORIGINAL string column → `TypeError` at CI runtime. The separate-statement
structure avoids it, so the post-heal script is correct.

## PERMANENT changes made alongside (do NOT revert these)
- `agents/tools.py`: new `validate_generated_code` check that flags a numeric comparison **chained**
  onto `pd.to_numeric()`/`.astype()` (the `.where(chunk[col] >= 0)` runtime-crash pattern) — caught
  locally so it never burns CI minutes. Tested by `tests/test_chained_coerce_compare.py`.

## Revert (restore the correct standard) — line ~109 back to:
```
**Numeric columns — coerce with `pd.to_numeric` (NEVER `.astype(float)`), in a SEPARATE statement before comparing.** A numerically compared/clamped column may carry dirty values. Coerce, **assign back FIRST**, THEN compare/clamp on the now-numeric column. `.astype(float)` raises `ValueError` on the first bad value (validator catches it). Chaining the comparison onto the coerce reads the ORIGINAL `str` column (not yet assigned) → `TypeError: Invalid comparison between dtype=str and int` — a RUNTIME-only crash the validator can't see.
```python
# ❌ .where reads the un-coerced (string) column → TypeError at runtime
chunk['ad_spend'] = pd.to_numeric(chunk['ad_spend'], errors='coerce').where(chunk['ad_spend'] >= 0, other=0)
# ✅ coerce + assign back FIRST, THEN clamp
chunk['ad_spend'] = pd.to_numeric(chunk['ad_spend'], errors='coerce')
chunk['ad_spend'] = chunk['ad_spend'].fillna(0).clip(lower=0)   # non-numeric→NaN→0, negative→0
```
(`.astype('Int64')` for the final integer cast is separate — see Storage.)
```
- Line ~125 worked example back to: `chunk['unit_price'] = pd.to_numeric(chunk['unit_price'], errors='coerce')  # dirty/non-numeric → NaN`
- Then: `make ingest` (verify NO `Failed to generate embedding` + `✅ Ingested: python_standards.md`), `rm DEMO_MISTAKES.md`.
- ⚠️ Token budget: python_standards.md sits near the 8191-token embedding limit — confirm < 8191 after editing.

## ARM (before the run): `make ingest` already done → the architect reads it from Pinecone.
Run `global_marketing` via Streamlit (agent local, free). Watch: architect `.astype(float)` →
validator FAILED → Medic fix → CLEAN → push → CI green. One heal, generation phase.
