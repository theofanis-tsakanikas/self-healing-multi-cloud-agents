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

# text-embedding-3-small hard per-input limit (what the OpenAI API actually rejects at).
EMBED_MODEL = "text-embedding-3-small"
EMBED_TOKEN_LIMIT = 8191
# We count with tiktoken locally, but the OpenAI embeddings endpoint counts ~1.4% HIGHER (observed:
# a file tiktoken measured at 8190 was rejected by the API at 8302). So local tiktoken passing the
# raw 8191 limit does NOT guarantee the API accepts it. Enforce a SAFE CEILING with margin instead —
# tokens below this are guaranteed under the real API limit. This is the number the guard + CI test use.
EMBED_TOKEN_SAFE_CEILING = 8000
# Standards above this are getting close to the safe ceiling and should be split BEFORE more content.
EMBED_TOKEN_WARN = 7700


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
    """Return (token_count, within_safe_ceiling) for a file's raw UTF-8 content. Uses the SAFE ceiling
    (not the raw API limit) because tiktoken under-counts vs the API — see EMBED_TOKEN_SAFE_CEILING."""
    tokens = count_tokens(Path(path).read_text(encoding="utf-8"))
    return tokens, tokens <= EMBED_TOKEN_SAFE_CEILING
