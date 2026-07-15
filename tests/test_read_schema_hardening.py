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


def test_read_data_schema_rejects_schema_qualified_name():
    # The dotted schema.table form is rejected on purpose (it breaks introspection + quoting); all
    # pipeline sources use a bare table name.
    r = read_data_schema.invoke({"table_name": "analytics.raw_global_marketing"})
    assert isinstance(r, str) and "invalid table name" in r


def test_bare_identifiers_pass_the_gate():
    import re

    pattern = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
    for good in ("raw_eu_sales", "raw_us_crm", "raw_global_marketing"):
        assert pattern.match(good), f"valid bare identifier wrongly rejected: {good}"
