"""Evidence-quote provenance — a fabricated request_fix quote has no match in real outputs."""
from langchain_core.messages import AIMessage, ToolMessage

from agents.medic import _evidence_has_provenance


def test_provenance_true_when_quote_appears_in_a_real_tool_output():
    msgs = [ToolMessage(content="VALIDATION FAILED: Error: undefined name 'foo'", tool_call_id="1")]
    assert _evidence_has_provenance("Error: undefined name 'foo'", msgs) is True


def test_provenance_false_when_quote_is_fabricated():
    msgs = [ToolMessage(content="...Everything looks green!", tool_call_id="1")]
    assert _evidence_has_provenance("Error: a failure that never appeared in any log", msgs) is False


def test_provenance_is_whitespace_and_case_insensitive():
    msgs = [AIMessage(content="Traceback:   KeyError:   'campaign'")]
    assert _evidence_has_provenance("traceback: keyerror: 'campaign'", msgs) is True
