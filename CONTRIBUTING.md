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
