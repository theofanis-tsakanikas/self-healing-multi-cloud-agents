# ADR-0005: The verification fetch happens in Python, not in the LLM's turn

- **Status:** Accepted
- **Date:** 2026-06 (recorded 2026-08)

## Context

After the Infra agent pushes and CI starts, the Medic must poll GitHub Actions until the run
resolves. The natural implementation gives the model a `fetch_github_action_logs` tool and lets it
decide when to call it.

It does not work, and the way it fails is instructive. `gpt-4o-mini` followed the prompt's instruction
to *"tell the user you are waiting and finish your turn"* — and **skipped the re-fetch** on the second
poll. The loop stalled; the supervisor's LLM fallback then FINISHed with `mission_status` unset, which
[ADR-0004](0004-bounded-autonomy-fail-closed.md) correctly fails closed as `MissionFailedError` — on
an otherwise successful deployment.

It was masked locally, where the deploy is usually green by the first poll, and surfaced only when the
agent ran **inside CI** and the deploy workflow was still `QUEUED`.

## Decision

Take the decision away from the model. On every verification-phase entry — `infra_status == "completed"`
with a `last_push_sha` — the **node itself** calls `fetch_github_action_logs` and decides:

- green → `mission_status = "verified"`
- `PENDING` → re-poll
- a real failure → fall through to the LLM for diagnosis and `request_fix`

Five retries with exponential backoff: 30s → 60s → 120s → 240s → 300s. After the fifth the run is
**terminal**: the Medic sets `fix_loop_escalated` and `mission_status = "ci_unverified"`, and the
supervisor routes FINISH **deterministically** — never through the LLM fallback, which used to re-enter
polling with a fresh counter and could sleep for hours toward the recursion limit.

One special case stays out of the fix loop: a `403` from the log fetch returns
`PENDING: PERMISSIONS_ERROR`. That is a token-scope problem, not a code defect, so the Medic tells the
operator to fix `GH_TOKEN` and does **not** call `request_fix`.

## Alternatives rejected

- **The LLM decides when to poll.** The failure above. Waiting is not judgment; it is control flow.
- **A single long sleep.** Wastes time when CI is fast and still times out when it is slow.
- **Unlimited polling.** Turns a queued workflow into an hours-long run that ends at the recursion
  limit rather than at a diagnosis.

## Consequences

- The poll loop cannot stall, and its terminal state is reached by code rather than by the model
  choosing to stop.
- This is a specific instance of the general rule in [ADR-0002](0002-the-llm-deterministic-boundary.md):
  the fetch is mechanically determined, so the model does not own it.
- The backoff schedule is a fixed budget. A CI run slower than ~12 minutes reports `ci_unverified` —
  honest, and better than a green tick nobody checked.
