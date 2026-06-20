"""
Regression for #7 (2026-06-20): the architect fix prompt injects the CURRENT on-disk content of the
target file so patch_project_file's `old` matches reality — symmetric to the infra agent. In a
CI-runtime script heal the script was written many turns ago and trimmed from context, so without
this the LLM patches blind. Only architect-owned files (.py / .sql / dashboards / requirements) are
injected; infra-owned files (.tf / k8s / Dockerfile) are NOT.
"""
import os

os.environ.setdefault("LANGCHAIN_TRACING_V2", "false")
os.environ.setdefault("LANGSMITH_TRACING", "false")


class _Msg:
    def __init__(self, content):
        self.content = content


def test_inject_current_script_content(tmp_path, monkeypatch):
    import agents.architect as arch
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "pipe_x.py").write_text(
        "chunk['qty'] = chunk['qty'].astype(float)   # the buggy line\n"
    )
    monkeypatch.setattr(arch, "REPO_ROOT", tmp_path)

    hc = "Target file to fix: scripts/pipe_x.py — replace .astype(float) with pd.to_numeric(..., errors='coerce')"
    block = arch._inject_current_file_contents(hc)
    assert "astype(float)" in block            # the REAL current line is visible to patch
    assert "CURRENT ON-DISK CONTENT" in block


def test_infra_owned_files_not_injected_by_architect():
    import agents.architect as arch
    # .tf / k8s / Dockerfile belong to infra → the architect never injects them
    assert arch._inject_current_file_contents("fix terraform/main.tf") == ""
    assert arch._inject_current_file_contents("fix k8s/job.yaml") == ""
    assert arch._inject_current_file_contents("a generic message with no file path") == ""
