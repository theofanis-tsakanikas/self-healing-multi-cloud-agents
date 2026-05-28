import os
import re
import json
import logging
from pathlib import Path
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage, ToolMessage
from agents.llm_factory import get_llm
from agents.state import AgentState
from agents.constants import TEMPERATURE, PROMPTS_DIR, SUPERVISOR_PROMPT_FILE
from utils.prompt_utils import format_prompt
from utils.file_utils import read_file
from utils.message_utils import collect_message_text_blobs, trailing_tool_batch

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("SUPERVISOR")

PROJECT_ROOT = Path(__file__).resolve().parent.parent

def _normalize_medic_target(raw: str) -> str | None:
    """Map LLM strings to standard agent keys."""
    key = (raw or "").strip().lower()
    if any(x in key for x in ("architect", "code", "logic")): return "architect"
    if any(x in key for x in ("infra", "terraform", "docker", "k8s", "ci")): return "infra"
    return None

def _extract_medic_fix_target(messages) -> str | None:
    """Extract intended agent for a fix from Medic's tool calls or messages."""
    pool = trailing_tool_batch(messages) or messages[-5:]
    for message in reversed(pool):
        for candidate in collect_message_text_blobs(message):
            if "request_fix" not in candidate and "REJECTED_BY_MEDIC" not in candidate:
                continue
            # Parse the JSON payload and read target_agent directly to avoid
            # false keyword matches (e.g. "app" inside "apply" routing to architect).
            try:
                data = json.loads(candidate)
                target_agent = data.get("target_agent", "")
                hit = _normalize_medic_target(target_agent)
                if hit:
                    return hit
            except (json.JSONDecodeError, AttributeError):
                pass
            # Fallback: keyword scan on the raw text if JSON parsing fails
            hit = _normalize_medic_target(candidate)
            if hit:
                return hit
    return None

def supervisor_node(state: AgentState):
    """
    Main Orchestrator: Routes based on Unified State Statuses (Agnostic & Deterministic).
    """
    llm = get_llm(temperature=TEMPERATURE)

    # 1. LOAD SYSTEM PROMPT
    try:
        prompt_path = os.path.join(PROMPTS_DIR, SUPERVISOR_PROMPT_FILE)
        raw_template = read_file(prompt_path)
        # Using project_folder_name from state or constants
        raw_configs = state.get("raw_configs", {})
        project_folder_name = raw_configs.get("pipeline", {}).get("project_folder_name", "multi-cloud-self-healing-agent")
        system_prompt = format_prompt(raw_template, project_folder_name=project_folder_name)
    except Exception as e:
        system_prompt = "Lead Orchestrator mode. Guide the team based on architect_status and infra_status."

    # 2. STATE ANALYSIS (Using the new Status Schema)
    last_step = state.get("last_agent", "None")
    # Updated keys
    arch_status = state.get("architect_status", "pending") 
    infra_status = state.get("infra_status", "pending")
    
    recent_messages = state["messages"][-5:] if state["messages"] else []
    # Scan only AIMessages — ToolMessages contain retrieved knowledge base content
    # which includes error examples (SyntaxError, ImportError, etc.) that would
    # cause false-positive routing to Medic.
    normalized_last = " ".join(
        str(m.content) for m in recent_messages
        if isinstance(m, AIMessage) and not isinstance(m, ToolMessage)
    ).lower()
    
    logger.info(f"Supervisor | Last Agent: {last_step} | Arch Status: {arch_status} | Infra Status: {infra_status}")

    # 3. DETERMINISTIC ROUTING (State-Driven Logic)

    # RULE A: Architect Logic Flow
    if last_step == "architect":
        # Primary check: explicit error flag set by architect_node
        if state.get("agent_error"):
            logger.warning("Architect explicit error flag set. Routing to MEDIC.")
            return {"next_step": "medic", "agent_error": False}

        # Status check before keyword scan: if architect explicitly set "completed",
        # trust that signal. Keyword scan sees stale medic error messages in last 5
        # and produces false positives that override a legitimate completion.
        if arch_status == "completed":
            logger.info("Architect phase COMPLETED. Routing to INFRA.")
            return {"next_step": "infra"}

        # Fallback check: keyword scan only when status is still pending
        arch_errors = [
            "failed", "error", "exception", "syntaxerror",
            "importerror", "typeerror", "keyerror", "traceback",
            "nameerror", "attributeerror", "valueerror"
        ]
        if any(x in normalized_last for x in arch_errors):
            logger.warning("Architect failure detected via keyword scan. Routing to MEDIC.")
            return {"next_step": "medic"}

        logger.info("Architect phase PENDING. Returning to ARCHITECT.")
        return {"next_step": "architect"}

    # RULE B: Infrastructure & Deployment Flow
    if last_step == "infra":
        # Primary check: explicit error flag set by infra_node
        if state.get("agent_error"):
            logger.warning("Infra explicit error flag set. Routing to MEDIC.")
            return {"next_step": "medic", "agent_error": False}

        # Fallback check: keyword scan on last 5 messages
        infra_errors = ["failed", "error", "403", "404", "denied", "exception", "crashloop", "imagepull"]
        if any(x in normalized_last for x in infra_errors):
            logger.warning("Infra/Deployment failure detected via keyword scan. Routing to MEDIC.")
            return {"next_step": "medic"}
        
        # 2. Status Check (The gate to Medic)
        if infra_status == "completed":
            logger.info("🎯 Infra phase COMPLETED. Routing to MEDIC for validation.")
            return {"next_step": "medic"}

        # 3. Continuity Rule
        logger.info("Infra phase still PENDING. Staying with INFRA.")
        return {"next_step": "infra"}

    # RULE C: Medic Healing & Rejection Logic
    if last_step == "medic":
        medic_target = _extract_medic_fix_target(state["messages"])
        
        if medic_target == "architect":
            logger.info("Medic requested Logic fix. Resetting architect_status to pending.")
            return {
                "next_step": "architect",
                "architect_status": "pending" # Resetting unified status
            }
        
        if medic_target == "infra":
            logger.info("Medic requested Infra fix. Resetting infra_status to pending.")
            return {
                "next_step": "infra",
                "infra_status": "pending" # Resetting unified status
            }

        # Pending logs — Medic signaled it needs another turn
        if state.get("next_step") == "medic":
            logger.info("Medic signaled pending logs. Re-routing to MEDIC.")
            return {"next_step": "medic"}

        # Final Sign-off
        if "verified" in normalized_last or "alignment_ok" in normalized_last:
            logger.info("✅ Mission accomplished. System signaling FINISH.")
            return {"next_step": "FINISH"}

    # 4. LLM FALLBACK (Enhanced with State Context)
    # We pass the actual status values to the LLM so it makes an informed decision
    state_summary = f"""
    --- CURRENT STATE ---
    Task: {state.get('task')}
    Architect Status: {arch_status}
    Infrastructure Status: {infra_status}
    Last Agent: {last_step}
    Error Log: {state.get('error_log', 'None')}
    """
    
    messages = [
        SystemMessage(content=system_prompt), 
        HumanMessage(content=f"{state_summary}\nDecide the next agent (ARCHITECT, INFRA, MEDIC, or FINISH):")
    ]
    
    try:
        response = llm.invoke(messages)
        decision = response.content.upper().strip()
        decision = re.sub(r'[^A-Z]', '', decision)
    except Exception as e:
        logger.error(f"LLM Supervisor Fallback Error: {e}")
        decision = "MEDIC"

    node_map = {"ARCHITECT": "architect", "INFRA": "infra", "MEDIC": "medic", "FINISH": "FINISH"}
    return {"next_step": node_map.get(decision, "medic")}