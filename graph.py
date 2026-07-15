import logging
import os

from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode
from agents.state import AgentState
from utils.message_utils import collect_message_text_blobs, trailing_tool_batch
from agents.supervisor import supervisor_node
from agents.architect import architect_node
from agents.infra import infra_node
from agents.medic import medic_node

# Import the tools that the Medic uses
from agents.tools import (
    fetch_github_action_logs,
    query_vector_store,
    request_fix,
    store_architectural_insight
)

logger = logging.getLogger(__name__)

# --- CONFIGURATION ---

# Define which tools will be available in the Tools Node
medic_tools = [
    fetch_github_action_logs,
    query_vector_store,
    request_fix,
    store_architectural_insight
]
tools_node = ToolNode(medic_tools)

# --- GRAPH LOGIC ---

def should_continue(state: AgentState):
    """
    Decision function to check if the Medic needs to
    call a tool or return to the supervisor.
    """
    messages = state["messages"]
    last_message = messages[-1]

    # If the LLM called some tool (e.g. Pinecone)
    if getattr(last_message, "tool_calls", None):
        return "execute_tools"

    # If the LLM wrote text (response), return to the supervisor
    return "supervisor"


def route_after_medic_tools(state: AgentState) -> str:
    """
    After ToolNode: if request_fix ran, skip another Medic LLM turn and let the Supervisor
    route to architect/infra using REJECTED_BY_MEDIC (avoids infinite tool loops in medic).
    Only inspects the latest trailing tool batch so stale handoffs do not fire.
    """
    for msg in trailing_tool_batch(state.get("messages", [])):
        for blob in collect_message_text_blobs(msg):
            if "REJECTED_BY_MEDIC" in blob:
                return "supervisor"
    return "medic"

# --- GRAPH DEFINITION ---

workflow = StateGraph(AgentState)

# Add Nodes
workflow.add_node("supervisor", supervisor_node)
workflow.add_node("architect", architect_node)
workflow.add_node("infra", infra_node)
workflow.add_node("medic", medic_node)
workflow.add_node("execute_tools", tools_node)

# --- EDGES ---

workflow.add_edge("architect", "supervisor")
workflow.add_edge("infra", "supervisor")

# The DYNAMIC LOGIC FOR THE MEDIC

workflow.add_conditional_edges(
    "medic",
    should_continue,
    {
        "execute_tools": "execute_tools",
        "supervisor": "supervisor"
    }
)

# request_fix -> Supervisor for deterministic handoff; other tools -> Medic continues reasoning
workflow.add_conditional_edges(
    "execute_tools",
    route_after_medic_tools,
    {
        "supervisor": "supervisor",
        "medic": "medic",
    },
)

# Entry Point & Supervisor Logic (Remain as is)
workflow.set_entry_point("supervisor")

workflow.add_conditional_edges(
    "supervisor",
    lambda x: x["next_step"],
    {
        "architect": "architect",
        "infra": "infra",
        "medic": "medic",
        "FINISH": END
    }
)

def _build_checkpointer():
    """Opt-in DURABLE state so a crash mid-heal can resume instead of losing the whole run.

    Default (no LANGGRAPH_CHECKPOINT_DB): return None → `compile()` behaves exactly as before, so no
    validated run changes. Set LANGGRAPH_CHECKPOINT_DB=<path.sqlite> for crash-durable SQLite state.

    We construct SqliteSaver DIRECTLY from a connection — NOT via `SqliteSaver.from_conn_string`, which
    is a @contextmanager that returns a context-manager object (compiling with it yields an app whose
    "checkpointer" has no get_tuple/put and crashes on first stream). And if the opt-in is requested but
    the package is missing, we FAIL LOUD rather than silently falling back to a non-durable MemorySaver
    (which would break the very durability promise the env var asks for).
    """
    db = os.getenv("LANGGRAPH_CHECKPOINT_DB")
    if not db:
        return None
    try:
        import sqlite3

        from langgraph.checkpoint.sqlite import SqliteSaver
    except ImportError as e:
        raise RuntimeError(
            "LANGGRAPH_CHECKPOINT_DB is set but langgraph-checkpoint-sqlite is not installed. "
            "Install it (`uv add langgraph-checkpoint-sqlite`) for durable resume, or unset the env var."
        ) from e
    conn = sqlite3.connect(db, check_same_thread=False)
    return SqliteSaver(conn)


def build_app(checkpointer=None):
    """Compile the graph. Pass a checkpointer, or rely on LANGGRAPH_CHECKPOINT_DB; default = none."""
    cp = checkpointer if checkpointer is not None else _build_checkpointer()
    return workflow.compile(checkpointer=cp) if cp is not None else workflow.compile()


app = build_app()
