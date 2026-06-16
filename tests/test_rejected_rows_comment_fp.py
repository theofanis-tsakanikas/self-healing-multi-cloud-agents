"""
Regression: validate_generated_code must check rejected_rows accumulation in CODE only,
not in COMMENTS. The model echoes the standard's own warning ("Do NOT keep an in-loop
`rejected_rows += ...`") verbatim as a comment; a raw substring scan flagged that guidance
text → false VALIDATION FAILED that dead-looped the self-heal (observed on
pipe_move_data_post_progress_to_adl_to_azure, 2026-06-16). A genuine in-loop `+=` must
still fail.
"""
import os
import tempfile

os.environ.setdefault("LANGCHAIN_TRACING_V2", "false")
os.environ.setdefault("LANGSMITH_TRACING", "false")

from agents.tools import validate_generated_code


def _validate(src: str) -> str:
    d = tempfile.mkdtemp()
    f = os.path.join(d, "pipe_x.py")
    with open(f, "w") as fh:
        fh.write(src)
    return str(validate_generated_code.invoke({"filename": f}))


_CORRECT_WITH_COMMENT = '''import pandas as pd
def run():
    rejected_by_reason = {}
    for i, chunk in enumerate([]):
        # Do NOT keep an in-loop `rejected_rows += ...` counter — the scalar total is
        # DERIVED after the loop as sum(rejected_by_reason.values()).
        rejected_by_reason['r'] = rejected_by_reason.get('r', 0) + 1
    rejected_rows = sum(rejected_by_reason.values())
    return rejected_rows
'''

_REAL_INLOOP = '''import pandas as pd
def run():
    rejected_by_reason = {}
    rejected_rows = 0
    for i, chunk in enumerate([]):
        rejected_rows += len(chunk)
    return rejected_rows
'''


def test_instructional_comment_is_not_flagged():
    out = _validate(_CORRECT_WITH_COMMENT)
    assert "rejected_rows must be DERIVED" not in out, out


def test_real_inloop_accumulation_still_fails():
    out = _validate(_REAL_INLOOP)
    assert "VALIDATION FAILED" in out
    assert "rejected_rows must be DERIVED" in out
