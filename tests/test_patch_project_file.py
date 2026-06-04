"""Unit tests for tools.patch_project_file — surgical fix-mode edits.

The syntax safety-net is the guard that stopped the self-heal death spiral: a patch
that would turn a parseable .py into a broken one is rejected and the file is left
untouched (so the loop can't keep re-applying a corrupting patch).
"""
from agents.tools import patch_project_file


def _write(p, text):
    p.write_text(text, encoding="utf-8")
    return str(p)


class TestSuccessfulPatch:
    def test_replacement_applies_and_file_changes(self, tmp_path):
        f = _write(tmp_path / "x.py", "a = 1\n")
        result = patch_project_file.invoke({
            "filename": f,
            "replacements": [{"old": "a = 1", "new": "a = 2"}],
        })
        assert "PATCH APPLIED" in result
        assert (tmp_path / "x.py").read_text() == "a = 2\n"

    def test_old_not_found_is_skipped(self, tmp_path):
        f = _write(tmp_path / "x.py", "a = 1\n")
        result = patch_project_file.invoke({
            "filename": f,
            "replacements": [{"old": "does_not_exist", "new": "y"}],
        })
        assert "not found" in result
        assert (tmp_path / "x.py").read_text() == "a = 1\n"  # unchanged


class TestSyntaxSafetyNet:
    def test_patch_breaking_python_is_rejected_and_file_unchanged(self, tmp_path):
        original = "def f():\n    return 1\n"
        f = _write(tmp_path / "x.py", original)
        # Replacing the body with an unterminated paren is a SyntaxError.
        result = patch_project_file.invoke({
            "filename": f,
            "replacements": [{"old": "    return 1", "new": "    return ("}],
        })
        assert "PATCH REJECTED" in result
        assert (tmp_path / "x.py").read_text() == original  # rolled back

    def test_empty_elif_stub_is_rejected(self, tmp_path):
        # The exact failure mode from the Azure self-heal loop: a comment-only elif.
        original = (
            "if _CLOUD == 'azure':\n"
            "    host = 1\n"
        )
        f = _write(tmp_path / "x.py", original)
        result = patch_project_file.invoke({
            "filename": f,
            "replacements": [{
                "old": "if _CLOUD == 'azure':\n    host = 1\n",
                "new": "if _CLOUD == 'azure':\n    host = 1\nelif _CLOUD == 'aws':\n    # add later\n",
            }],
        })
        assert "PATCH REJECTED" in result
        assert (tmp_path / "x.py").read_text() == original


class TestMissingFile:
    def test_missing_file_returns_error(self, tmp_path):
        result = patch_project_file.invoke({
            "filename": str(tmp_path / "nope.py"),
            "replacements": [{"old": "a", "new": "b"}],
        })
        assert "does not exist" in result


class TestAddImportDirective:
    def test_add_import_inserts_after_last_import(self, tmp_path):
        f = _write(tmp_path / "x.py", "import os\nimport sys\n\nx = 1\n")
        result = patch_project_file.invoke({
            "filename": f,
            "replacements": [{"old": "__ADD_IMPORT__", "new": "from utils.cloud_config import cloud_get"}],
        })
        assert "PATCH APPLIED" in result
        content = (tmp_path / "x.py").read_text()
        assert "from utils.cloud_config import cloud_get" in content
        # inserted within the import block, before the code body
        assert content.index("cloud_get") < content.index("x = 1")

    def test_add_import_skipped_when_already_present(self, tmp_path):
        f = _write(tmp_path / "x.py", "from utils.cloud_config import cloud_get\nx = 1\n")
        result = patch_project_file.invoke({
            "filename": f,
            "replacements": [{"old": "__ADD_IMPORT__", "new": "from utils.cloud_config import cloud_get"}],
        })
        assert "already present" in result


class TestRequirementsNormalization:
    def test_requirements_path_normalised_to_root(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "requirements.txt").write_text("pandas\n", encoding="utf-8")
        # caller passes scripts/requirements.txt — must normalise to the root file
        result = patch_project_file.invoke({
            "filename": "scripts/requirements.txt",
            "replacements": [{"old": "pandas", "new": "pandas\ns3fs"}],
        })
        assert "PATCH APPLIED" in result
        assert "s3fs" in (tmp_path / "requirements.txt").read_text()
