# ADR-0004: Bounded autonomy, and `verified` as the only success

- **Status:** Accepted
- **Date:** 2026-06 (recorded 2026-08)

## Context

An agent that repairs its own deployments needs an answer to two questions that every demo of this
kind avoids: when does it stop, and what counts as having worked?

Without a bound, a fix loop facing a problem it cannot solve will keep trying until it hits a
recursion limit — burning tokens and cloud time on the same error. And without a definition of
success, "the graph ran to completion" quietly becomes the definition, which is how an agent reports
green on a deployment that never worked.

## Decision

**The loop is bounded**: three identical errors, or eight total rounds. Reaching either is not a
crash; it is a documented outcome that exits non-zero, turns CI red, and surfaces the Medic's last
diagnosis to a human.

**Success has one name.** `mission_status` is a terminal contract with four values:

| Value | Meaning |
|---|---|
| `"verified"` | The Medic verified the deployment end-to-end — **the only success** |
| `"escalated"` | The fix loop was abandoned, or an operational blocker was hit |
| `"ci_unverified"` | The CI result never arrived within the polling budget |
| `""` | An unverified FINISH, e.g. the supervisor's LLM fallback — **fails closed** |

Every entry point consumes it. `main.py` throws `MissionFailedError` *into* the stream on an
unverified FINISH, so the LangSmith root run records an error and the process exits 1, turning the
GitHub Action red. Streamlit shows a failure banner rather than the success one.

**"The graph ran to completion" is never success by itself.**

## Alternatives rejected

- **Unbounded retry.** Converges only by accident, and fails expensively when it does not.
- **Success on graph completion.** The default if nobody decides otherwise, and the reason so many
  agent demos end green regardless of outcome.
- **Fail-open on an unset status.** Treating `""` as success would make every unhandled path a silent
  pass — precisely the paths most likely to be wrong.

## Consequences

- Every agent demo ends green. This one is **allowed to end red**, because a system that pretends to
  succeed is worse than one that admits it failed.
- A red run carries the exact diagnosis, so the human starts where the agent stopped.
- The bound is a real ceiling: a problem needing nine rounds is escalated rather than solved. That is
  the accepted cost of not having an unbounded one.
