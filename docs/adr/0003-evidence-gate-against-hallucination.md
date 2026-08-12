# ADR-0003: A fix request is refused without a verbatim quote from real output

- **Status:** Accepted
- **Date:** 2026-06 (recorded 2026-08)

## Context

The Medic's job is to read a failed CI run and route a fix. The failure mode that makes such an agent
worse than useless is a confident diagnosis of a problem that does not exist: it patches a clean file,
the real error survives, and the loop burns attempts on fiction.

Asking the model to "only report real errors" does not work. Left to scan raw messages, it discovers
errors that are not there, and a plausible quote is as easy to generate as a real one.

## Decision

Two mechanisms, both outside the model's discretion.

**The evidence is assembled in Python, not discovered by the LLM.** `medic.py` parses
`state["messages"]` at the Python layer into a structured validation summary — FAILED files with their
verbatim error text, and CLEAN files — and injects that into the prompt. The model reasons over a
summary it did not build.

**The tool refuses a call without provenance.** `request_fix` takes a required `evidence_quote`, and
the tool **rejects the call** unless the quote contains a real error marker (`VALIDATION FAILED`,
`Error:`, `Traceback`, `exit code`, plus the kubectl markers `is invalid` / `Invalid value` /
`immutable`). Only FAILED files may be passed; **clean files are off-limits**, and a green run cannot
be "fixed".

The marker list is a maintenance surface, and its failure mode is known: a genuine CI error whose text
matches no marker is *wrongly rejected*, leaving `medic_fix_target` empty so the supervisor falls back
to its default owner and routes an infra fix to the architect. Adding a marker when a new failure
shape appears is part of handling that shape.

## Alternatives rejected

- **Trusting the prompt.** "Only fix real errors" is not a constraint; it is a wish.
- **Post-hoc validation of the patch.** Catches a bad patch but not the wasted round, and does nothing
  about the loop's attempt budget.
- **A confidence threshold.** Confidence is not provenance. A hallucination can be delivered
  confidently; a quote either appears in real output or it does not.

## Consequences

- A hallucinated fix has nothing to route to — the tool call fails before the graph moves.
- The Medic's judgment becomes measurable offline: the eval harness scores routing and the evidence
  gate against a corpus of 17 failure classes with **no LLM, no cloud and no keys**, because both are
  deterministic.
- The marker list must cover every real failure source the Medic will quote. It is the one place where
  being too strict costs a correct diagnosis, which is why the miss-mode is documented rather than
  assumed away.
