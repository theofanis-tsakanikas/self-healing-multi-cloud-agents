"""Shared pytest fixtures and global test isolation.

These tests are HERMETIC: no network, no cloud, no real credentials. This file
guarantees that by (1) providing dummy credentials so module-level client init
never reads real keys, and (2) patching the heavy external SDK constructors
(Pinecone, OpenAI, OpenAIEmbeddings) before any test module imports `agents.tools`
— which instantiates them at module load. The patches stay active for the whole
session and are stopped on teardown.
"""
import os
from unittest.mock import MagicMock, patch

# ── 1. Dummy credentials / region (set before any agent module import) ──────────
# load_dotenv(override=False) inside the agent modules will NOT override these, so
# even a developer's populated .env cannot leak real keys into a test run.
os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")
os.environ.setdefault("PINECONE_API_KEY", "test-pinecone-key")
os.environ.setdefault("PINECONE_INDEX_NAME", "test-index")
os.environ.setdefault("AWS_DEFAULT_REGION", "eu-central-1")

# ── 1b. LangSmith tracing OFF — hermetic tests must not post telemetry ──────────
# agents/tools.py runs load_dotenv() at import, so a developer .env with
# LANGCHAIN_TRACING_V2=true would make every direct tool .invoke() in this suite
# surface as a ROOT trace in the team's LangSmith project (tests call tools with
# no parent run — that's exactly where the stray validate_generated_code /
# patch_project_file / request_fix root traces came from; real agent runs nest
# fine). Direct assignment (not setdefault) wins over both
# load_dotenv(override=False) AND an exported shell variable.
os.environ["LANGCHAIN_TRACING_V2"] = "false"
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_API_KEY"] = "test-langsmith-key"

# ── 2. Patch external SDK constructors at collection time ───────────────────────
# agents/tools.py does `client = OpenAI()`, `OpenAIEmbeddings(...)`, `Pinecone(...)`
# at module level. Patching the constructors here (before the first import of
# agents.tools by any test module) makes those no-ops that never touch the network.
_PATCHERS = [
    patch("pinecone.Pinecone", MagicMock()),
    patch("openai.OpenAI", MagicMock()),
    patch("langchain_openai.OpenAIEmbeddings", MagicMock()),
]
for _p in _PATCHERS:
    _p.start()


def pytest_unconfigure(config):
    """Stop the session-wide SDK patches when the test run ends."""
    for _p in _PATCHERS:
        try:
            _p.stop()
        except RuntimeError:
            pass


import pytest  # noqa: E402  (import after the patches are started, by design)


@pytest.fixture(autouse=True)
def _clear_bootstrap_cache():
    """`_load_bootstrap_outputs` is lru_cached — clear it around every test so a
    monkeypatched/real read in one test never leaks into the next."""
    from utils.cloud_config import _load_bootstrap_outputs
    _load_bootstrap_outputs.cache_clear()
    yield
    _load_bootstrap_outputs.cache_clear()
