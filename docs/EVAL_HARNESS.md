# Offline replay + eval harness (`evals/`)

The agent's most distinctive capability — the **Medic** diagnosing a failed CI run and
routing an evidence-grounded fix — was also its least verifiable: you needed four cloud
accounts, a Pinecone index, and real spend to see it, and the LLM nodes had **no evals** (the
routing/guard logic grew as a whack-a-mole of regex signatures pinned to one model). This
harness closes both gaps, entirely offline.

## The two layers of the Medic's judgment

| Layer | What it is | How it's evaluated |
|---|---|---|
| **Routing + evidence gate** | Pure Python: `agents.medic._ci_error_owner` + the failing-file fallback (`_extract_ci_failed_file` → `_owner_of_file`) decide architect vs infra; `agents.tools.request_fix` refuses any diagnosis whose `evidence_quote` holds no real error marker (`_EVIDENCE_MARKERS`). | **Replay mode** — deterministic, no LLM. `evals.harness.deterministic` calls the *real* functions; `evals.harness.runner` scores them against the corpus and writes `evals/report/`. |
| **Diagnosis / fix quality** | The LLM's judgment: given a CI log, does it call `request_fix` with the right target and verbatim, gate-valid evidence? | **Eval mode** — `evals.harness.eval_live` runs a live model (any provider via `get_llm`) with the real `request_fix` tool + gate. Needs an LLM key; catches model regressions. |

## The corpus — `evals/corpus/corpus.json`

Each case is a realistic failing-CI log reconstructed from the documented failure classes
(`DEMO_MISTAKES.md`, `CLAUDE.md`) and the Medic's own routing signatures. The **real** git
self-heal commits are fix-only (no triggering logs are stored anywhere in the repo), so the
trigger logs here are synthesised to match the exact strings the deterministic router keys
on — with `real_self_heal_commit` linking the classes that actually happened (e.g. the
Databricks secret-key mismatch, commit `0b1e0c1`). Cases cover both owners (script-logic →
architect; missing library/secret/resource → infra) and negatives (clean / green / marker-less
speculation the gate must refuse).

## Running it

```bash
make eval-replay      # offline: score the corpus, regenerate evals/report/ (no key, no cloud)
make eval-check       # CI parity: fail if the report is stale or a deterministic check regressed
make heal LOG=run.log # route + validate ANY failing CI log through the real Medic logic (offline)

# eval mode (needs an LLM key) — model-agnostic:
LLM_MODEL=gpt-4o OPENAI_API_KEY=...        make eval-live
LLM_MODEL=claude-3-5-sonnet-latest ANTHROPIC_API_KEY=... make eval-live model=claude-3-5-sonnet-latest
```

`evals/report/REPORT.md` + `metrics.json` are committed and CI-checked (`make eval-check`),
and the whole replay layer is also asserted by `tests/test_evals.py` — so editing a routing
signature or the marker list flips a corpus case and fails the build. The regression net the
whack-a-mole never had.

## `heal` — the judgment as a standalone tool

`evals/heal.py` decouples the healing *judgment* from the pipelines this agent generated:
point it at a failure log from **any** repo/run and it answers "which agent owns this, and is
it a genuine failure?" using the shipping routing + gate — offline. `--diagnose` adds a
live-LLM root-cause. This is the agent's diagnostic core, reusable outside its own loop.
