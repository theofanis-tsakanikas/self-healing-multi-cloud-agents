"""Unit tests for the cross-agent state lifecycle invariants.

These cover the deterministic handoff rules that were extracted into pure helpers:
  - healing_context accumulates across multiple request_fix calls (never overwrites),
  - medic_fix_requested Scenario A (clear) vs B (keep) at the architect→infra handoff.
The full LLM-driven node execution is integration/eval-deferred by design.
"""
from agents.medic import _accumulate_healing_context
from agents.architect import _keep_medic_fix_requested


class TestHealingContextAccumulation:
    def test_first_chunk_seeds_context(self):
        assert _accumulate_healing_context("", "fix A") == "fix A"

    def test_second_chunk_appends_not_overwrites(self):
        result = _accumulate_healing_context("fix A", "fix B")
        assert "fix A" in result and "fix B" in result
        assert result.index("fix A") < result.index("fix B")

    def test_chunks_are_separated(self):
        assert "---" in _accumulate_healing_context("fix A", "fix B")

    def test_empty_new_chunk_leaves_context_unchanged(self):
        assert _accumulate_healing_context("fix A", "") == "fix A"

    def test_three_calls_keep_all(self):
        ctx = ""
        for chunk in ("A", "B", "C"):
            ctx = _accumulate_healing_context(ctx, chunk)
        assert all(c in ctx for c in ("A", "B", "C"))


class TestMedicFixRequestedHandoff:
    def test_scenario_a_github_not_done_clears_flag(self):
        # infra hasn't pushed yet → flag cleared so infra starts fresh
        assert _keep_medic_fix_requested(True, github_done=False) is False

    def test_scenario_b_github_done_keeps_flag(self):
        # infra already pushed → keep flag so infra skips terraform, goes to push
        assert _keep_medic_fix_requested(True, github_done=True) is True

    def test_no_fix_requested_stays_false(self):
        assert _keep_medic_fix_requested(False, github_done=True) is False
        assert _keep_medic_fix_requested(False, github_done=False) is False
