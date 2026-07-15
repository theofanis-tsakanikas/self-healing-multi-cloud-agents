"""Regression net for the Pinecone embedding token budget.

The knowledge base is ingested one vector PER FILE with no chunking, so every standard must fit the
embed model's 8191-token per-input limit. If one grows past it, the ingest fails and (before the
loud-fail guard) silently left a STALE version live in Pinecone. This test refuses a PR that pushes
any standard over the limit BEFORE it can poison the vector store — the visible half of the guard in
`scripts/ingest_to_pinecone.py`.
"""
from pathlib import Path

import pytest

from utils import embedding_budget
from utils.embedding_budget import (
    EMBED_TOKEN_SAFE_CEILING,
    EMBED_TOKEN_WARN,
    count_tokens,
    count_tokens_detail,
)

_KB = Path(__file__).resolve().parent.parent / "knowledge_base"
_STANDARDS = sorted(_KB.rglob("*.md"))


def test_knowledge_base_has_standards():
    # Guard against the glob silently matching nothing (which would make the budget test vacuous).
    assert _STANDARDS, f"No standards found under {_KB}"


def test_check_file_reports_within_limit():
    tokens, within = embedding_budget.check_file(_STANDARDS[0])
    assert isinstance(tokens, int) and tokens > 0
    assert within is True


def test_count_tokens_falls_back_when_tiktoken_unavailable(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "tiktoken":
            raise ImportError("simulated: no tiktoken")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert count_tokens("a" * 40) == 10  # conservative chars/4, rounded up


@pytest.mark.parametrize("path", _STANDARDS, ids=lambda p: p.name)
def test_standard_within_embedding_token_limit(path):
    tokens, accurate = count_tokens_detail(path.read_text(encoding="utf-8"))
    if not accurate:
        pytest.skip("tiktoken unavailable — a chars/4 estimate over-counts; the ingest lets the API verify")
    assert tokens <= EMBED_TOKEN_SAFE_CEILING, (
        f"{path.relative_to(_KB.parent)} is {tokens} tokens > {EMBED_TOKEN_SAFE_CEILING} — it would be "
        f"SILENTLY DROPPED by ingest and served stale from Pinecone. Split it into two standards "
        f"(and fetch both at retrieval time)."
    )
    if tokens > EMBED_TOKEN_WARN:
        # Not a failure, but surfaces the near-limit standards so the next editor knows to split
        # BEFORE adding content rather than after an overflow.
        print(
            f"NEAR LIMIT: {path.name} at {tokens}/{EMBED_TOKEN_SAFE_CEILING} tokens "
            f"({EMBED_TOKEN_SAFE_CEILING - tokens} to spare) — split before adding more."
        )
