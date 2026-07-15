"""Embedding token-budget guard — shared by the Pinecone ingest and its regression test.

`text-embedding-3-small` has an 8191-token per-input limit. The knowledge base is ingested one
vector PER FILE (no chunking), so a standard that grows past the limit makes the OpenAI embed call
fail — and historically the ingest SILENTLY skipped that file, leaving a STALE version live in
Pinecone while the edit looked applied. This module makes the limit explicit so:

  * `scripts/ingest_to_pinecone.py` can fail LOUD (non-zero exit) instead of silently skipping, and
  * `tests/test_embedding_budget.py` can enforce the budget on every standard in CI, so a PR that
    pushes a standard over the limit is refused before it can poison the vector store.
"""
from __future__ import annotations

from pathlib import Path

# text-embedding-3-small hard per-input limit.
EMBED_MODEL = "text-embedding-3-small"
EMBED_TOKEN_LIMIT = 8191
# Standards above this are dangerously close and should be split BEFORE more content is added.
EMBED_TOKEN_WARN = 7800


def count_tokens(text: str) -> int:
    """Token count for EMBED_MODEL.

    Uses tiktoken's cl100k_base (the encoder text-embedding-3-small uses) when available; falls back
    to a conservative chars/4 estimate that slightly OVER-counts, so the fallback fails safe toward
    the limit rather than under-reporting an overflow.
    """
    try:
        import tiktoken

        return len(tiktoken.get_encoding("cl100k_base").encode(text))
    except Exception:
        return (len(text) + 3) // 4


def check_file(path: str | Path) -> tuple[int, bool]:
    """Return (token_count, within_limit) for a file's raw UTF-8 content."""
    tokens = count_tokens(Path(path).read_text(encoding="utf-8"))
    return tokens, tokens <= EMBED_TOKEN_LIMIT
