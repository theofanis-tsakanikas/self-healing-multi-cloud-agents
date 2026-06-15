"""
Regression: fetch_github_action_logs builds the workflow filename as f"{project_id}_pipeline.yml",
so project_id MUST resolve to the BARE pipeline name. A caller that passes the TIMESTAMPED runtime
PROJECT_ID ('PIPE_EU_SALES_TO_S3-20260615-0820') used to produce a non-existent workflow path →
GitHub 404 → a SUCCESSFUL deploy was wrongly reported ci_unverified (observed 2026-06-15). The
function now normalises project_id, so BOTH forms resolve to 'pipe_eu_sales_to_s3_pipeline.yml'.
"""
from unittest.mock import patch

import agents.tools as tools


def _run_fetch(project_id: str):
    """Invoke fetch_github_action_logs with the GitHub layer mocked; return (output, urls)."""
    urls = []

    def _fake_get(url, token):
        urls.append(url)
        if "/runs?head_sha=" in url:          # the workflow-runs list lookup
            return {"workflow_runs": [{"id": 999}]}
        return {"status": "completed", "conclusion": "success"}   # the run metadata

    with patch.object(tools, "_github_token", return_value="tok"), \
         patch.object(tools, "_github_repository_explicit", return_value=None), \
         patch.object(tools, "_github_repository_from_env", return_value=("owner", "repo")), \
         patch.object(tools, "_github_get_json", side_effect=_fake_get):
        out = tools.fetch_github_action_logs.invoke(
            {"project_id": project_id, "head_sha": "abc1234"})
    return out, urls


_EXPECTED = "pipe_eu_sales_to_s3_pipeline.yml/runs"


def test_bare_pipeline_name_resolves_correct_workflow():
    out, urls = _run_fetch("pipe_eu_sales_to_s3")
    assert any(_EXPECTED in u for u in urls), urls
    assert "everything looks green" in out.lower()


def test_timestamped_project_id_resolves_same_workflow():
    # The exact bug: the timestamped PROJECT_ID must NOT produce
    # 'PIPE_EU_SALES_TO_S3-20260615-0820_pipeline.yml' (→ 404).
    out, urls = _run_fetch("PIPE_EU_SALES_TO_S3-20260615-0820")
    assert any(_EXPECTED in u for u in urls), urls
    assert not any("20260615" in u for u in urls), "timestamp leaked into the workflow path"
    assert "everything looks green" in out.lower()


def test_uppercase_bare_name_is_lowercased():
    _out, urls = _run_fetch("PIPE_EU_SALES_TO_S3")
    assert any(_EXPECTED in u for u in urls), urls
