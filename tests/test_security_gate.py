"""Adversarial proof that the generated-infrastructure security gate holds.

Mirrors the governance platform's `gate-proof`: the gate must PASS the clean v1.0.0 goldens (the real
artifacts from the validated runs) and REFUSE the deliberately-unsafe fixtures — one violation per
HIGH rule. If conftest is installed, the Rego second engine is cross-checked against the Python gate.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from policy.security_analyzer import analyze, derive_findings, extract_context

_ROOT = Path(__file__).resolve().parent.parent
_CLEAN = _ROOT / "tests" / "goldens" / "v1.0.0"
_UNSAFE = _ROOT / "policy" / "fixtures" / "unsafe"
_REGO_DIR = _ROOT / "policy" / "opa"

_EXPECTED_UNSAFE_RULES = {
    "DOCKERFILE_ROOT_USER",
    "DOCKERFILE_COPIES_ENV",
    "K8S_INLINE_SECRET",
    "IMAGE_PUBLIC_LATEST",
    "WORKFLOW_INLINE_SECRET",
}


def test_clean_goldens_pass_the_gate():
    result = analyze(_CLEAN)
    highs = [f for f in result["findings"] if f["severity"] == "HIGH"]
    assert result["passed"] is True, f"clean v1.0.0 goldens should pass; got HIGH findings: {highs}"
    assert result["high_count"] == 0


def test_unsafe_fixture_is_refused():
    result = analyze(_UNSAFE)
    assert result["passed"] is False
    fired = {f["rule"] for f in result["findings"] if f["severity"] == "HIGH"}
    assert fired == _EXPECTED_UNSAFE_RULES, f"unsafe fixture should trip every HIGH rule; got {fired}"


@pytest.mark.parametrize("rule", sorted(_EXPECTED_UNSAFE_RULES))
def test_each_high_rule_fires_on_the_unsafe_fixture(rule):
    fired = {f.rule for f in derive_findings(extract_context(_UNSAFE)) if f.severity == "HIGH"}
    assert rule in fired


def _run_conftest(context: dict) -> set[str]:
    """Return the set of Rego deny messages' rule prefixes, via conftest."""
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(context, fh)
        ctx_path = fh.name
    proc = subprocess.run(
        ["conftest", "test", ctx_path, "--policy", str(_REGO_DIR), "-o", "json"],
        capture_output=True,
        text=True,
    )
    try:
        report = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        return set()
    rules = set()
    for result in report:
        for failure in result.get("failures", []):
            msg = failure.get("msg", "")
            rules.add(msg.split(":", 1)[0])
    return rules


@pytest.mark.skipif(shutil.which("conftest") is None, reason="conftest (OPA) not installed — Python gate is the enforced control")
def test_rego_second_engine_agrees_with_python_gate():
    for target in (_CLEAN, _UNSAFE):
        result = analyze(target)
        py_rules = {f["rule"] for f in result["findings"] if f["severity"] == "HIGH"}
        rego_rules = _run_conftest(result["context"])
        assert rego_rules == py_rules, f"Python and Rego disagree on {target.name}: py={py_rules} rego={rego_rules}"
