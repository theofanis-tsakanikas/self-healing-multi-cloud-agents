---
id: python-requirements-standard
applies_to: aws, azure, gcp (object-storage)
primary_consumer: codegen (render_requirements) + medic reference
enforced_by: agents/codegen.py (deterministic render) + validate_generated_code
last_reviewed: 2026-07-15
---

# Requirements Standard

> **GENERATION: CODE-OWNED.** `requirements.txt` is rendered deterministically by
> `agents/codegen.py:render_requirements` — the architect never writes it. This
> file is the SPEC for that generator (and the Medic's reference). It was split out
> of `python_standards.md` so that standard stays under the embedding token limit —
> a code-owned spec does not belong in the LLM-owned generation standard anyway.

**File location:** `requirements.txt` at the **repository root** — never inside `scripts/` or any subdirectory.

The file is the shared block PLUS the active cloud's block. Copy the matching cloud block **verbatim — omit NOTHING**. Each cloud needs THREE distinct things and dropping any one fails at runtime:
- object-storage SDK · `to_parquet()` filesystem driver · DB driver

**Shared (always):**
```
pandas
sqlalchemy
pyarrow
trino
prometheus-client
```
**AWS — append all three:**
```
boto3
s3fs
psycopg2-binary
```
**GCP — append all three:**
```
google-cloud-storage
gcsfs
pymysql
```
**Azure — append all three:**
```
azure-storage-blob
adlfs
psycopg2-binary
```
(Use `pyodbc` instead of `psycopg2-binary` ONLY if the source is Azure SQL / MSSQL — not Postgres.)

The filesystem driver (`s3fs` / `gcsfs` / `adlfs`) and the DB driver (`psycopg2-binary` / `pymysql`) are BOTH mandatory: without the filesystem driver `to_parquet()` cannot write to a cloud URI; without the DB driver SQLAlchemy cannot connect.
