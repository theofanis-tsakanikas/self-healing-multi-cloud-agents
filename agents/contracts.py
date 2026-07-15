"""Load-bearing string contracts between graph nodes — the single registry.

These exact substrings are WIRE CONTRACTS: one node/tool emits them, another matches on them. Changing
a value silently breaks a hop (the failure surfaces only at runtime, often in CI). They are gathered
here as the documented source of record, and `tests/test_contracts.py` asserts each still appears in
its producer, so a drift fails the hermetic suite instead of a live run.

Migrating every call site to import these constants (instead of hardcoding the literal) is incremental
future work; today this module + its test are the guardrail against silent drift.
"""

# Medic handoff — request_fix emits this; graph.route_after_medic_tools + supervisor match on it.
REJECTED_BY_MEDIC = "REJECTED_BY_MEDIC"

# CI success — fetch_github_action_logs emits these; medic (auto-poll) + supervisor treat them as green.
CI_GREEN_MARKERS = ("No failed jobs found", "Everything looks green!")

# push_to_github success line — infra parses the 40-hex SHA out of it (last_push_sha).
PUSH_SUCCESS_PREFIX = "STATUS: SUCCESS"

# validate_generated_code failure header — medic's VALIDATION SUMMARY + evidence gate key on it.
VALIDATION_FAILED = "VALIDATION FAILED"

# infra auto-validation failure — medic's _AUTOVAL_FAIL_RE recovers the target filename from it.
AUTOVAL_FAILED_PREFIX = "AUTO-VALIDATION FAILED"

# execute_terraform state-lock short-circuit — medic escalates instead of calling request_fix.
STATE_LOCK_ERROR = "STATE_LOCK_ERROR"

# supervisor terminal signal. (There is deliberately NO LLM-prose success token — success is the
# deterministic mission_status=="verified" only; the former ALIGNMENT_OK path was removed as a
# fail-closed hole.)
INFRA_COMPLETE = "INFRA_COMPLETE"

# k8s job.yaml image sentinel — the CI `sed` step rewrites it to the real registry URL.
RESOLVE_FROM_TF = "RESOLVE_FROM_EXECUTE_TERRAFORM_OUTPUT"

# The terminal mission-status contract (agents/state.py) — "verified" is the ONLY success.
MISSION_VERIFIED = "verified"
MISSION_ESCALATED = "escalated"
MISSION_CI_UNVERIFIED = "ci_unverified"
