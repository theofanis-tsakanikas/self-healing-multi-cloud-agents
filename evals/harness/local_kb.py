"""Offline knowledge-base retriever — a local stand-in for `query_vector_store` (Pinecone).

Ingestion stores each `knowledge_base/*.md` whole (no chunking), and the Medic reaches
retrieval only through a `fetch` lambda / the `query_vector_store` seam that returns a
string — so a local keyword retriever over the same files is a faithful, credential-free
substitute for the eval harness's live mode. Returns the same `🛡️ [OFFICIAL SPEC]`-prefixed
format the real tool produces.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_KB_DIR = _REPO_ROOT / "knowledge_base"


@lru_cache(maxsize=1)
def _docs() -> tuple[tuple[str, str], ...]:
    out = []
    if _KB_DIR.is_dir():
        for p in sorted(_KB_DIR.rglob("*.md")):
            out.append((str(p.relative_to(_REPO_ROOT)), p.read_text(encoding="utf-8")))
    return tuple(out)


def local_query(query: str, top_k: int = 3, snippet_chars: int = 2000) -> str:
    """Keyword-score the KB files against `query`; return the top matches, formatted like
    the production retriever. Empty result mirrors the real tool's miss string."""
    terms = [t for t in re.findall(r"[a-z0-9_]+", query.lower()) if len(t) > 2]
    scored: list[tuple[int, str, str]] = []
    for name, text in _docs():
        low = text.lower()
        score = sum(low.count(t) for t in terms)
        if score:
            scored.append((score, name, text))
    scored.sort(key=lambda s: (-s[0], s[1]))
    if not scored:
        return "No relevant guidelines found."
    return "\n\n".join(f"🛡️ [OFFICIAL SPEC] {name}\n{text[:snippet_chars]}" for _, name, text in scored[:top_k])
