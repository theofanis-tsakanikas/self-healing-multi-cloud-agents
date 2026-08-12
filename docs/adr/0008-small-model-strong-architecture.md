# ADR-0008: `gpt-4o-mini`, with the reliability in the architecture

- **Status:** Accepted
- **Date:** 2026-06 (recorded 2026-08)

## Context

The obvious way to make an agent that generates Terraform and diagnoses CI failures more reliable is
to use a stronger model. It works, up to a point, and it hides everything underneath: a system that is
reliable *because* the model is good has no defence when the model changes, and no evidence about
which of its parts are actually load-bearing.

It is also the expensive answer to a question that may not need it. This agent makes many calls per
run — routing decisions, phase-gated tool calls, diagnosis, patches — and most of them are not hard.

## Decision

Run on **`gpt-4o-mini`**, and put the reliability in the harness instead:

- **Deterministic routing.** The Supervisor emits exactly one word; the routing invariants are Python.
- **Phase-gated tool calls.** One tool per phase, `tool_choice="required"`, pre-computed arguments —
  the model chooses content, not sequence.
- **The evidence gate.** A fix request without a verbatim quote is refused
  ([ADR-0003](0003-evidence-gate-against-hallucination.md)).
- **Golden-tested code generation.** Everything mechanically determined is rendered from config and
  pinned to the `v1.0.0` artifacts ([ADR-0002](0002-the-llm-deterministic-boundary.md)).
- **A validator as a safety net**, enforcing policy before anything reaches CI.
- **Deterministic control flow** where the model was observed to skip a step
  ([ADR-0005](0005-deterministic-ci-polling.md)).

The claim this supports is narrow and testable: four clouds deployed and self-healed end-to-end, on a
small model, because the architecture — not the model — is what refuses a hallucinated fix, stops an
unconverging loop, and renders the artifacts that have one correct answer.

## Alternatives rejected

- **A frontier model.** Would raise the floor and obscure which controls matter. It is also the wrong
  lesson: an agent that only works on the best available model is a bet on the vendor, not a design.
- **A model-agnostic claim with no default.** The repository supports OpenAI, Anthropic and Vertex AI
  keys, but naming what it was *validated* on is the honest form.

## Consequences

- Every control in the harness earns its place, because nothing else is carrying it.
- The failure modes are documented and specific rather than "the model was having a bad day" — the
  skipped re-poll, the double-braced f-string, the mangled Lakeview widget encoding. Each became a
  guarantee.
- A stronger model would make some guards look redundant. They are not: they are what makes the
  behaviour reproducible when the model changes underneath.
- Verdict quality on the hardest judgment calls is the floor of what this architecture supports, not
  its ceiling.
