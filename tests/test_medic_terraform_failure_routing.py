"""INTEGRATION regression: a `terraform apply` failure during the infra node MUST route the fix to
INFRA deterministically — even when the LLM's request_fix quote is rejected by the provenance gate.

The bug this guards (live run 29399147996): the infra terraform apply failed with
`FAILED: Terraform apply\nERROR: … Insufficient versioning_configuration blocks …`. The Medic's LLM
called request_fix but PARAPHRASED the error ("Error: Insufficient versioning_configuration blocks"),
which is not a verbatim substring of the tool output, so the provenance gate dropped it,
fix_requested stayed False, the infra failure fell through to the architect default, and
architect↔medic looped to the graph recursion limit (200) — the run CRASHED instead of healing.

`_terraform_command_failure` now detects the FAILED-terraform marker in Python and forces an infra
fix, bypassing the LLM quote gate. This drives medic_node end-to-end to prove the routing."""
from unittest.mock import MagicMock, patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

import agents.medic as medic_mod
from agents.medic import _terraform_command_failure, medic_node

_TF_FAIL = (
    "FAILED: Terraform apply\n"
    "ERROR: \n"
    'Error: Insufficient versioning_configuration blocks\n'
    '  on main.tf line 21, in resource "aws_s3_bucket_versioning" "versioning":\n'
    "  21: resource \"aws_s3_bucket_versioning\" \"versioning\" {\n"
    "At least 1 \"versioning_configuration\" block is required.\n"
    "OUTPUT: "
)


def _diagnosis_state():
    # Infra just failed terraform apply → NOT completed → diagnosis phase (no CI auto-poll).
    return {
        "task": "build", "messages": [
            HumanMessage(content="infra provisioning"),
            ToolMessage(content=_TF_FAIL, tool_call_id="tf1"),
        ],
        "error_log": "", "project_id": "PIPE-X-20260715-1200", "config_path": "",
        "target_infra": "aws", "written_files": ["terraform/main.tf"],
        "infra_provisioned": False, "infra_status": "pending",
        "architect_status": "completed", "schema_discovered": True,
        "github_done": False, "last_push_sha": "", "medic_fix_requested": False,
        "ci_poll_attempt": 0, "fix_attempt": 0, "last_fix_signature": "",
        "medic_fix_target": "", "mission_status": "",
    }


def test_terraform_failure_helper_detects_and_supersedes():
    assert _terraform_command_failure([ToolMessage(content=_TF_FAIL, tool_call_id="t")])
    # A later SUCCESS (re-apply after heal) supersedes the earlier failure → nothing to fix.
    healed = [
        ToolMessage(content=_TF_FAIL, tool_call_id="t1"),
        ToolMessage(content="SUCCESS: Terraform apply\nApply complete!", tool_call_id="t2"),
    ]
    assert _terraform_command_failure(healed) == ""
    # Operational lock error is not a code bug.
    lock = [ToolMessage(content="PENDING: STATE_LOCK_ERROR — could not acquire lock", tool_call_id="t")]
    assert _terraform_command_failure(lock) == ""


def test_terraform_apply_failure_routes_to_infra_despite_rejected_quote():
    # The LLM paraphrases the error (adds an "Error: " prefix not adjacent in the real output) →
    # the provenance gate rejects it → fix_requested would stay False on the old code.
    ai = AIMessage(content="diagnosing", tool_calls=[{
        "name": "request_fix",
        "args": {
            "target_agent": "architect",  # even a WRONG target must be overridden to infra
            "issue_description": "terraform versioning block",
            "suggested_fix": "add versioning_configuration",
            "evidence_quote": "Error: Insufficient versioning_configuration blocks (paraphrased)",
        },
        "id": "c1",
    }])
    llm_with_tools = MagicMock()
    llm_with_tools.invoke.return_value = ai
    llm = MagicMock()
    llm.bind_tools.return_value = llm_with_tools

    with patch.object(medic_mod, "get_llm", return_value=llm), \
         patch.object(medic_mod, "store_architectural_insight", MagicMock()), \
         patch.object(medic_mod.time, "sleep", MagicMock()):
        out = medic_node(_diagnosis_state())

    assert out.get("medic_fix_target") == "infra", (
        f"terraform apply failure was NOT routed to infra (got "
        f"{out.get('medic_fix_target')!r}) — it would fall to the architect default and loop."
    )
    assert out.get("infra_status") == "pending"  # infra reset for the fix cycle
    assert "terraform/main.tf" in out.get("healing_context", "")
    assert out.get("mission_status", "") != "verified"
