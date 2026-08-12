# ADR-0002: The LLM owns judgment; deterministic code owns everything else

- **Status:** Accepted
- **Date:** 2026-06 (recorded 2026-08)

## Context

Early on, every artifact was LLM-generated: the pipeline script, the SQL, the Terraform, the
Dockerfile, `requirements.txt`, six Kubernetes manifests, the Grafana dashboard spec, and the deploy
workflow for four clouds.

Most of those have exactly one correct answer given the config. The model still got them wrong
intermittently — dropping a cloud-SDK import, double-braceing an f-string, nesting a dashboard widget
encoding into invalid JSON — so repair code accumulated underneath each one. At that point the
generator existed twice: once as a prompt, once as the Python that fixed the prompt's output.

## Decision

Score every artifact by **input variability**, and let that decide who owns it.

| Owner | Artifacts | Why |
|---|---|---|
| **LLM** | The pipeline script (pandas or Spark), the SQL DDL, the pipeline Terraform, all diagnosis | Open inputs: an arbitrary source schema, natural-language business rules, an error log |
| **Code** (`agents/codegen.py`) | `requirements.txt`, the Dockerfile, all six K8s manifests, the dashboards, the deploy workflow for all four clouds | Mechanically determined: naming, structure, wiring, boilerplate |

The distinction that matters is between two kinds of post-processing:

- **Repair** — margin fixes to genuine LLM output (injecting the SDK import its own call requires,
  un-doubling braces). Defensive; keep it.
- **Replace** — the code regenerates the whole artifact and keeps nothing. That is the signal the LLM
  step is vestigial: **remove it and render from config instead.**

Every code-owned artifact is golden-tested against the `v1.0.0` outputs, and its standard carries a
`GENERATION: CODE-OWNED` banner, because the standard is now the generator's specification and the
Medic's reference rather than a prompt.

## Alternatives rejected

- **LLM-generates-everything with repair underneath.** The state this replaced. An LLM step for a
  deterministic artifact, with a working generator sitting beneath it, is an expensive template engine.
- **Code-generates-everything.** Would need a template per source schema and per business rule — the
  open-input cases are precisely where a template cannot go.
- **No fallback to the LLM when codegen fails.** Deliberate: the Medic patches the file, and the
  permanent fix is the generator. A silent LLM fallback would hide a broken render.

## Consequences

- Variance collapsed where it was never acceptable, and stayed where it is inherent.
- A standard change for a code-owned artifact requires the matching render change in the same commit,
  or the two diverge.
- Knowing where **not** to use the LLM is the difference between an AI system and an expensive
  template engine — and it is the single thing this repository most wants a reviewer to notice.
