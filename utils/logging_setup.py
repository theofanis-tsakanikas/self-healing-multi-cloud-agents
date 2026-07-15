"""Opt-in structured logging.

Default (LOG_FORMAT unset) keeps the existing human-readable format, so nothing about a normal run
changes. `LOG_FORMAT=json` emits one JSON object per line — with a correlation id (`project_id`, the
per-run PROJECT_ID) on every record — for machine ingestion / log aggregation, which the previous
free-text logs could not support.
"""
import json
import logging
import os
import sys

_HUMAN_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"


class CorrelationFilter(logging.Filter):
    """Stamp every record with the current run's PROJECT_ID so logs are correlatable across nodes."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.project_id = os.getenv("PROJECT_ID", "-")
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "project_id": getattr(record, "project_id", None) or os.getenv("PROJECT_ID", "-"),
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(level: int = logging.INFO, logfile: str | None = "pipeline_execution.log") -> logging.Logger:
    """Configure root logging (stdout + optional file). Returns the orchestrator logger.

    Format is chosen from LOG_FORMAT: `json` → JsonFormatter, anything else → the human format.
    """
    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    fmt: logging.Formatter = JsonFormatter() if os.getenv("LOG_FORMAT", "").lower() == "json" else logging.Formatter(_HUMAN_FORMAT)
    corr = CorrelationFilter()

    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if logfile:
        handlers.append(logging.FileHandler(logfile, mode="a"))
    for h in handlers:
        h.setFormatter(fmt)
        h.addFilter(corr)
        root.addHandler(h)

    return logging.getLogger("PIPELINE_ORCHESTRATOR")
