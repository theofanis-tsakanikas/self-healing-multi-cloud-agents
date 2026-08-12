# Architecture Decision Records

What was chosen, what was rejected, and what it cost. The decisions were made during development
(2026-06) and recorded here in 2026-08 — the reasoning is drawn from `CLAUDE.md`, the prompts, the
standards and the code, not reconstructed after the fact.

| ADR | Decision | Rejected |
|---|---|---|
| [0001](0001-standards-first-generation.md) | Conventions are retrieved, versioned standards | Prompt folklore · patching generated artifacts · fine-tuning |
| [0002](0002-the-llm-deterministic-boundary.md) | The LLM owns judgment under variability; code owns what is mechanically determined | LLM-generates-everything with repair underneath · code-generates-everything |
| [0003](0003-evidence-gate-against-hallucination.md) | A fix request is refused without a verbatim quote from real output | Trusting the prompt · post-hoc patch validation · a confidence threshold |
| [0004](0004-bounded-autonomy-fail-closed.md) | A bounded loop, and `verified` as the only success | Unbounded retry · success on graph completion · fail-open |
| [0005](0005-deterministic-ci-polling.md) | The verification fetch happens in Python | Letting the model decide when to poll — it skipped, and the loop stalled |
| [0006](0006-no-default-cloud.md) | No default cloud; the provider is always read from config | One primary cloud with "support" for the others · an SDK abstraction layer |
| [0007](0007-databricks-as-a-distinct-execution-model.md) | Databricks as a fourth provider with its own execution model | Bending it into the object-storage model · a separate agent · leaving it out |
| [0008](0008-small-model-strong-architecture.md) | `gpt-4o-mini`, with the reliability in the harness | A frontier model · a model-agnostic claim with no validated default |

## The two worth reading first

[ADR-0002](0002-the-llm-deterministic-boundary.md) is the decision this repository most wants a
reviewer to notice: knowing where **not** to use the LLM is the difference between an AI system and an
expensive template engine.

[ADR-0005](0005-deterministic-ci-polling.md) is the one with the sharpest lesson, because it is a bug
report rather than a preference. The model followed its prompt, skipped a re-poll, stalled the loop,
and turned a successful deployment red. Waiting is control flow, not judgment.

## Format

Each record states **Context** (the forces, including what was tried first), **Decision** (what was
chosen, with the code that implements it), **Alternatives rejected** (and why), and **Consequences**
(including the ones that hurt). A decision that turns out to be wrong is superseded by a new record,
not edited — the ledger keeps the mistake.
