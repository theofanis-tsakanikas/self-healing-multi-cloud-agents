"""PII enforcement — sample-row masking + the pii_sensitive anonymization gate."""
from agents.tools import _mask_sample_rows, validate_generated_code


def _validate(tmp_path, content, name="pipe_x.py"):
    f = tmp_path / name
    f.write_text(content, encoding="utf-8")
    return validate_generated_code.invoke({"filename": str(f)})


def test_mask_sample_rows_redacts_strings_keeps_structure():
    rows = [{"name": "Alice", "age": 30, "email": "a@b.com", "active": True, "note": None}]
    m = _mask_sample_rows(rows)[0]
    assert m["name"] == "***REDACTED***" and m["email"] == "***REDACTED***"
    assert m["age"] == 30 and m["active"] is True and m["note"] is None


def test_mask_sample_rows_redacts_numeric_pii_by_column_name():
    # Phone/SSN stored as INTEGER, DOB as a date — masked by column name regardless of dtype;
    # a genuinely non-PII numeric (amount) stays visible for structure.
    rows = [{"phone_number": 5551234567, "ssn": 123456789, "amount": 42.5, "quantity": 3}]
    m = _mask_sample_rows(rows)[0]
    assert m["phone_number"] == "***REDACTED***" and m["ssn"] == "***REDACTED***"
    assert m["amount"] == 42.5 and m["quantity"] == 3


def test_pii_pipeline_without_anonymization_is_flagged(tmp_path, monkeypatch):
    monkeypatch.setenv("PII_SENSITIVE", "true")
    r = _validate(tmp_path, "import pandas as pd\nchunk['x'] = chunk['x'] + 1\n")
    assert "PII:" in r and "NO anonymization" in r


def test_pii_regex_false_masking_is_flagged(tmp_path, monkeypatch):
    monkeypatch.setenv("PII_SENSITIVE", "true")
    r = _validate(tmp_path, "import hashlib\nchunk['email'] = chunk['email'].str.replace('x', '*', regex=False)\n")
    assert "regex=False" in r


def test_pii_pipeline_with_hash_and_regex_passes_pii_gate(tmp_path, monkeypatch):
    monkeypatch.setenv("PII_SENSITIVE", "true")
    code = (
        "import hashlib\n"
        "chunk['name'] = chunk['name'].apply(lambda v: hashlib.sha256(str(v).encode()).hexdigest())\n"
        "chunk['email'] = chunk['email'].str.replace(r'.', '*', regex=True)\n"
    )
    assert "PII:" not in _validate(tmp_path, code)


def test_non_pii_pipeline_is_not_gated(tmp_path, monkeypatch):
    monkeypatch.setenv("PII_SENSITIVE", "false")
    assert "PII:" not in _validate(tmp_path, "import pandas as pd\nchunk['x'] = chunk['x'] + 1\n")
