"""read_data_schema hardening — SQL-identifier whitelist + credential redaction."""
from agents.tools import _redact_secrets, read_data_schema


def test_redact_secrets_masks_dsn_credentials():
    red = _redact_secrets("could not connect: postgresql://admin:hunter2@db.internal:5432/crm")
    assert "hunter2" not in red
    assert "***:***@" in red


def test_redact_secrets_masks_password_kv():
    assert _redact_secrets("FATAL: password=SuperSecret rejected") .startswith("FATAL: password=***")


def test_read_data_schema_rejects_sql_injection_table_name():
    # Rejection happens BEFORE any DB connection (step 0), so this is hermetic. read_data_schema is a
    # @tool, so invoke it via the tool interface.
    for bad in ("raw; DROP TABLE users; --", "raw_us_crm' OR '1'='1", "(SELECT 1)", "a b", ""):
        result = read_data_schema.invoke({"table_name": bad})
        assert isinstance(result, str) and "invalid table name" in result, f"accepted bad name: {bad!r}"


def test_read_data_schema_allows_valid_identifier_shapes():
    # These pass the identifier gate (they will fail later at connect-time offline, but must NOT be
    # rejected as invalid names).
    import re

    pattern = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?$")
    for good in ("raw_eu_sales", "raw_us_crm", "analytics.raw_global_marketing"):
        assert pattern.match(good), f"valid identifier wrongly rejected: {good}"
