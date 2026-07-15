"""get_llm robustness — a set-but-empty LLM_MODEL env must fall back, not crash."""
from agents.llm_factory import get_llm


def test_empty_llm_model_falls_back_and_does_not_raise(monkeypatch):
    # A GitHub Actions `vars.LLM_MODEL` that is defined-but-empty expands to "" — must not reach
    # _infer_provider("") (ValueError) or int("") (crash).
    monkeypatch.setenv("LLM_MODEL", "")
    monkeypatch.setenv("LLM_MAX_RETRIES", "")
    monkeypatch.setenv("LLM_TIMEOUT_SEC", "")
    llm = get_llm()  # dummy OPENAI_API_KEY from conftest; construction makes no API call
    assert llm is not None
