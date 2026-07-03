"""Offline tests for the Medic eval harness (evals/).

The headline is `test_corpus_deterministic_checks_all_pass`: the failure corpus, scored
against the REAL Medic routing + evidence-gate functions, must be 100% correct. That turns
the previously eval-less, whack-a-mole routing/guard logic into a regression net — edit a
signature list or the marker list and a corpus case flips, failing CI.
"""

from __future__ import annotations

from evals.harness import runner
from evals.harness.deterministic import evidence_gate_accepts, load_corpus, resolve_owner
from evals.harness.local_kb import local_query
from evals.heal import verdict


# --------------------------------------------------------------------------- #
# The regression net
# --------------------------------------------------------------------------- #
def test_corpus_deterministic_checks_all_pass():
    metrics = runner.evaluate(load_corpus())
    assert metrics["routing"]["failures"] == [], metrics["routing"]["failures"]
    assert metrics["evidence_gate"]["failures"] == [], metrics["evidence_gate"]["failures"]
    assert metrics["all_passed"] is True
    # The corpus must be non-trivial (both owners + negatives represented).
    assert metrics["routing"]["total"] >= 8
    assert metrics["evidence_gate"]["total"] >= metrics["routing"]["total"]


def test_committed_report_is_in_sync():
    # CI `--check` parity — the committed metrics.json + REPORT.md match a fresh render.
    assert runner.main(["--check"]) == 0


# --------------------------------------------------------------------------- #
# Routing (the real _ci_error_owner + file-frame fallback, composed)
# --------------------------------------------------------------------------- #
def test_script_logic_routes_to_architect():
    assert resolve_owner("Traceback ...\nKeyError: 'campaign'\nError: exit code 1") == "architect"
    assert resolve_owner("pyspark.sql.utils.AnalysisException: cannot resolve 'x'") == "architect"


def test_missing_resource_routes_to_infra():
    assert resolve_owner("Exception: Secret does not exist with scope: s and key: db_password") == "infra"
    assert resolve_owner("java.lang.ClassNotFoundException: org.postgresql.Driver") == "infra"


def test_trino_error_uses_file_frame_fallback():
    # No signature match -> the failing scripts/*.py frame decides -> architect.
    log = 'File "/app/scripts/pipe_eu_sales_to_s3.py", line 5\ntrino.exceptions.TrinoUserError: Unknown type \'TEXT\'\nError: 1'
    assert resolve_owner(log) == "architect"


def test_undetermined_when_no_signal():
    assert resolve_owner("some unrelated informational message") == ""


# --------------------------------------------------------------------------- #
# Evidence gate (the real request_fix tool)
# --------------------------------------------------------------------------- #
def test_evidence_gate_accepts_real_failure():
    assert evidence_gate_accepts("Traceback ...\nKeyError: 'x'\nError: exit code 1") is True
    assert evidence_gate_accepts("Secret does not exist with scope: s and key: db_password") is True


def test_evidence_gate_refuses_clean_and_speculation():
    assert evidence_gate_accepts("AUTO-VALIDATION: CLEAN ✓ — all checks passed.") is False
    assert evidence_gate_accepts("I think the schema might be wrong, we should change it.") is False
    assert evidence_gate_accepts("Everything looks green! No failed jobs found.") is False


# --------------------------------------------------------------------------- #
# heal CLI verdict + offline KB retriever
# --------------------------------------------------------------------------- #
def test_heal_verdict():
    v = verdict("Exception: Secret does not exist with scope: s and key: db_password")
    assert v["is_real_failure"] is True
    assert v["routed_to"] == "infra"

    clean = verdict("AUTO-VALIDATION: CLEAN ✓")
    assert clean["is_real_failure"] is False


def test_local_kb_retrieval():
    hit = local_query("databricks secret dbutils spark standard")
    assert "OFFICIAL SPEC" in hit
    assert local_query("zzzqqq_no_such_term_anywhere_xyz") == "No relevant guidelines found."
