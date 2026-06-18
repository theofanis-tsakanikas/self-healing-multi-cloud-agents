"""
Regression for the GCP self-heal flow (2026-06-18): the NL business rule produced
`target_criteria.column: campaign` and the architect used it literally → `chunk['campaign']`
while the real column is `campaign_id` → `KeyError: 'campaign'` at CI runtime (passed the
validator). validate_generated_code now cross-checks every chunk['<col>'] READ against the
schema cached by read_data_schema and flags non-existent columns LOCALLY.

CRITICAL invariants (protect the validated AWS/Azure/GCP/Databricks runs — ZERO false positives):
  - a real source column            → NOT flagged
  - a derived column created earlier → NOT flagged (chunk['is_suspicious'] = chunk['is_suspicious'] | …)
  - NO schema cached (fail-open)     → NOT flagged
  - script SELECTs a DIFFERENT table → NOT flagged (cache belongs to another pipeline)
  - a self-referential never-created column ('campaign') → FLAGGED
"""
import os

os.environ.setdefault("LANGCHAIN_TRACING_V2", "false")
os.environ.setdefault("LANGSMITH_TRACING", "false")

import tempfile

import agents.tools as T
from agents.tools import validate_generated_code

_SCHEMA = ["campaign_id", "platform_name", "ad_spend", "clicks", "impressions", "event_timestamp"]
_MARKER = "do NOT exist in the table schema"


def _script(ad_spend_and_campaign: str, table: str = "raw_global_marketing") -> str:
    return (
        "import pandas as pd\n"
        "def run():\n"
        f'    query = "SELECT * FROM {table}"\n'
        "    for chunk in pd.read_sql_query(query, None, chunksize=1000):\n"
        "        chunk['event_timestamp'] = pd.to_datetime(chunk['event_timestamp'], errors='coerce')\n"
        f"        {ad_spend_and_campaign}\n"
        "        chunk['is_suspicious'] = (chunk['clicks'] > chunk['impressions'])\n"
        "        chunk['is_suspicious'] = chunk['is_suspicious'] | (chunk['clicks'] >= 1000000)\n"
    )


def _validate(src: str) -> str:
    d = tempfile.mkdtemp()
    f = os.path.join(d, "scripts", "pipe_x.py")
    os.makedirs(os.path.dirname(f), exist_ok=True)
    with open(f, "w") as fh:
        fh.write(src)
    return str(validate_generated_code.invoke({"filename": f}))


def _set_cache(table, columns):
    T._LAST_SCHEMA_CACHE["table"] = table
    T._LAST_SCHEMA_CACHE["columns"] = columns


def teardown_function(_):
    T._LAST_SCHEMA_CACHE["table"] = None
    T._LAST_SCHEMA_CACHE["columns"] = None


_CAMPAIGN_BUG = "chunk['campaign'] = chunk['campaign'].where(chunk['campaign'].str.match(r'CMP'), other='X')"
_CAMPAIGN_OK = "chunk['campaign_id'] = chunk['campaign_id'].where(chunk['campaign_id'].str.match(r'CMP'), other='X')"


def test_unknown_column_is_flagged():
    _set_cache("raw_global_marketing", _SCHEMA)
    out = _validate(_script(_CAMPAIGN_BUG))
    assert _MARKER in out
    assert "campaign" in out


def test_correct_column_is_clean():
    _set_cache("raw_global_marketing", _SCHEMA)
    out = _validate(_script(_CAMPAIGN_OK))
    assert _MARKER not in out


def test_derived_column_created_earlier_not_flagged():
    # is_suspicious (not a source column) is created then re-read on a later line — must NOT flag.
    _set_cache("raw_global_marketing", _SCHEMA)
    out = _validate(_script(_CAMPAIGN_OK))
    assert "is_suspicious" not in out.split(_MARKER)[0] or _MARKER not in out


def test_fail_open_when_no_schema_cached():
    _set_cache(None, None)
    out = _validate(_script(_CAMPAIGN_BUG))
    assert _MARKER not in out  # no cache → check skipped, never false-positive


def test_skip_when_table_mismatch():
    # Cache belongs to a DIFFERENT pipeline's table → must not check this script.
    _set_cache("some_other_table", _SCHEMA)
    out = _validate(_script(_CAMPAIGN_BUG))
    assert _MARKER not in out
