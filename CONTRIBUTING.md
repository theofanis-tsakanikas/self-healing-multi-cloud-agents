# Contributing

## Local development setup

1. Copy the example env file and fill in your credentials:

   ```bash
   cp .env.example .env
   # Then open .env and replace every placeholder with a real value.
   ```

   `.env.example` is the canonical reference for every environment variable used
   across the codebase. Keep it up to date whenever you add a new `os.getenv()` call.

2. Install dependencies:

   ```bash
   make install  # runs uv sync
   ```

3. Run the tests:

   ```bash
   make test
   ```

## Dependency management

**Do not use `requirements.txt`.** All dependencies are managed via `pyproject.toml` and `uv`.

To add a dependency:

```bash
uv add <package>>=<version>
```

To install all dependencies locally:

```bash
make install  # runs uv sync
```

`pyproject.toml` is the single source of truth for all runtime and dev dependencies.

## Branch protection & review

`main` should be a protected branch — the repo is single-developer today, so this is the one process
control that most raises the bar:

- **No direct pushes to `main`.** Work on a feature branch and open a PR (`feat/…`, `fix/…`, `docs/…`).
- **Required status checks** before merge: `tests` (ruff + pytest + coverage floor + `make eval-check`)
  and `security` (gitleaks + trivy). The security gate (`make gate-proof`) and the eval regression net
  run inside the hermetic suite, so a green `tests` run already covers them.
- **Required review**: at least one approval. Solo? Use the PR checklist as a self-review gate and let
  CI be the second reviewer — never merge red.
- `CODEOWNERS` already flags `knowledge_base/` and `agents/prompts/`; a change there needs a Pinecone
  re-sync (`sync_knowledge_base: sync`) noted on the PR.

Enable via **GitHub → Settings → Branches → Add rule** for `main`: require PR, require the `tests` and
`security` checks, dismiss stale approvals.

## Local gates (mirror CI)

```bash
make lint          # ruff — same command CI runs
make test          # hermetic suite (no cloud, no credentials)
make gate-proof    # prove the generated-infra security gate holds
make eval-check    # Medic routing / evidence-gate regression net
```

## Where things live (design notes)

- **RAG backend is swappable by design.** Retrieval goes through `query_vector_store` (Pinecone) in
  production; `evals/harness/local_kb.py` is a faithful **offline** substitute (keyword-scored,
  same `🛡️ [OFFICIAL SPEC]` format) used by the eval harness with no network. To move off Pinecone
  (pgvector / OpenSearch), implement the same query→ranked-standards contract behind a backend flag —
  the offline substitute is the reference implementation. (A live-run validation is needed before
  switching the production path — see `docs/PROFESSIONALIZATION.md`.)
