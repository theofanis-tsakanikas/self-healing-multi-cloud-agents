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

# text-embedding-3-small hard per-input limit (what the OpenAI API rejects at). tiktoken's cl100k_base
# is the SAME tokenizer the API uses, so count_tokens matches the API exactly — provided tiktoken is
# installed (it is now a DIRECT dependency; see pyproject). If it ever isn't, count_tokens falls back
# to a chars/4 estimate that OVER-counts.
EMBED_MODEL = "text-embedding-3-small"
EMBED_TOKEN_LIMIT = 8191
# Enforce a SAFE CEILING a bit under the hard limit: it leaves headroom for tokenizer edge cases and
# keeps standards from creeping right up to the wall. This is the number the guard + CI test use.
EMBED_TOKEN_SAFE_CEILING = 8000
# Standards above this are getting close to the safe ceiling and should be split BEFORE more content.
EMBED_TOKEN_WARN = 7700


def count_tokens_detail(text: str) -> tuple[int, bool]:
    """Return (token_count, accurate). `accurate` is True only when tiktoken's cl100k_base (the exact
    encoder the embed API uses) produced the count; False when we fell back to a rough chars/4 estimate
    (tiktoken absent, or its one-time vocab download blocked). The fallback OVER-counts, so callers must
    NOT hard-block on an inaccurate count — a valid file would be false-rejected; let the API decide."""
    try:
        import tiktoken

        return len(tiktoken.get_encoding("cl100k_base").encode(text)), True
    except Exception:
        return (len(text) + 3) // 4, False


def count_tokens(text: str) -> int:
    """Token count for EMBED_MODEL (see count_tokens_detail for the accuracy caveat)."""
    return count_tokens_detail(text)[0]


def check_file(path: str | Path) -> tuple[int, bool]:
    """Return (token_count, within_safe_ceiling) for a file's raw UTF-8 content. Uses the SAFE ceiling
    (not the raw API limit) because tiktoken under-counts vs the API — see EMBED_TOKEN_SAFE_CEILING."""
    tokens = count_tokens(Path(path).read_text(encoding="utf-8"))
    return tokens, tokens <= EMBED_TOKEN_SAFE_CEILING
