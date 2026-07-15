"""Structured-logging unit tests — JSON formatter shape + correlation-id stamping."""
import json
import logging

from utils.logging_setup import CorrelationFilter, JsonFormatter


def _record(msg="hello", level=logging.INFO):
    return logging.LogRecord("some.logger", level, __file__, 1, msg, None, None)


def test_json_formatter_emits_valid_json_with_expected_fields(monkeypatch):
    monkeypatch.setenv("PROJECT_ID", "pipe-demo-20260715-1200")
    rec = _record("processed 68 rows")
    CorrelationFilter().filter(rec)
    payload = json.loads(JsonFormatter().format(rec))
    assert payload["level"] == "INFO"
    assert payload["logger"] == "some.logger"
    assert payload["msg"] == "processed 68 rows"
    assert payload["project_id"] == "pipe-demo-20260715-1200"
    assert "ts" in payload


def test_correlation_filter_defaults_when_no_project_id(monkeypatch):
    monkeypatch.delenv("PROJECT_ID", raising=False)
    rec = _record()
    CorrelationFilter().filter(rec)
    assert rec.project_id == "-"


def test_json_formatter_includes_exception(monkeypatch):
    monkeypatch.setenv("PROJECT_ID", "x")
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        rec = logging.LogRecord("l", logging.ERROR, __file__, 1, "failed", None, sys.exc_info())
    payload = json.loads(JsonFormatter().format(rec))
    assert "boom" in payload["exc"]
