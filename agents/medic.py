import os
import json
import logging
import random
import time
from pathlib import Path
from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, ToolMessage
from agents.llm_factory import get_llm
from agents.state import AgentState
from agents.tools import (
    fetch_github_action_logs,
    request_fix,
    query_vector_store,
    store_architectural_insight
)
from agents.constants import TEMPERATURE, PROMPTS_DIR, MEDIC_PROMPT_FILE
from utils.file_utils import read_file
from utils.prompt_utils import format_prompt
from utils.message_utils import safe_recent_messages

# Configure logging for the Medic agent
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("MEDIC")

load_dotenv()

_MAX_POLL_WAIT_SECONDS = 300  # 5-minute ceiling per backoff step

def medic_node(state: AgentState):
    """
    Medic (Diagnostic) Node: Analyzes logs and failures.
    Responsible for resetting state flags based on automated diagnostics.
    """
    logger.info("--- STARTING MEDIC (DIAGNOSTIC) NODE ---")

    # Phase-gated tool binding:
    # DIAGNOSIS phase  (infra not yet complete): request_fix + query_vector_store only.
    #   → The fix hasn't been applied yet — storing an insight now would pollute
    #     dynamic-experience with unverified solutions.
    # VERIFICATION phase (infra completed, CI logs available): adds fetch_github_action_logs
    #   + store_architectural_insight so the LLM can confirm success and immediately
    #   persist the validated fix as institutional knowledge.
    tools = [request_fix, query_vector_store]

    if state.get("infra_status") == "completed":
        tools.append(fetch_github_action_logs)
        tools.append(store_architectural_insight)
        logger.info("🔓 VERIFICATION phase: GitHub Logs + store_architectural_insight UNLOCKED")
    else:
        logger.info("🔒 DIAGNOSIS phase: store_architectural_insight locked until fix is verified")

    llm = get_llm(temperature=TEMPERATURE)
    llm_with_tools = llm.bind_tools(tools)
    
    project_id = state.get("project_id", "Unknown")
    target_infra = state.get("target_infra", "Unknown")
    
    # 1. LOAD SYSTEM PROMPT
    try:
        prompt_path = os.path.join(PROMPTS_DIR, MEDIC_PROMPT_FILE)
        raw_template = read_file(prompt_path)
        system_prompt = format_prompt(raw_template, project_id=project_id, target_infra=target_infra)
        
        # Adding instructions for the new infra_status flag
        system_prompt += (
            "\n- If infra/deployment fails: use request_fix(target_agent='infra')."
            "\n- If logs are still PENDING: tell the user you are waiting and finish your turn."
            "\n- If logs contain PERMISSIONS_ERROR: inform the user their GH_TOKEN needs 'actions: read' scope — do NOT call request_fix, this is a GitHub token configuration issue."
            "\n- If everything is perfect: explicitly state 'VERIFIED' or 'COMPLIANT'."
        )

        last_push_sha = state.get("last_push_sha", "")
        if last_push_sha:
            system_prompt += (
                f"\n\n**MANDATORY**: The last deployment was pushed with commit SHA '{last_push_sha}'. "
                f"Always pass head_sha='{last_push_sha}' when calling fetch_github_action_logs "
                f"so you examine the exact run triggered by that push."
            )
    except Exception as e:
        system_prompt = "Diagnostic mode active. Analyze CI/CD logs."

    # 2. CONTEXT PREPARATION
    # We include more history for Medic to see the previous Infra logic
    messages = [{"role": "system", "content": system_prompt}] + safe_recent_messages(state["messages"], limit=10)
    
    new_messages_for_state = []
    reset_architect = False
    reset_infra = False
    logs_still_pending = False
    verification_successful = False # To know if we will store the insight

    # 3. REASONING LOOP
    fix_requested = False
    healing_context = ""  # Populated when request_fix is called; written to state for next agent
    for i in range(5):
        response = llm_with_tools.invoke(messages)
        messages.append(response)
        new_messages_for_state.append(response)

        if not response.tool_calls:
            if "VERIFIED" in response.content.upper() or "COMPLIANT" in response.content.upper():
                verification_successful = True
            break

        for tool_call in response.tool_calls:
            tool_name = tool_call["name"]
            tool_args = tool_call["args"]

            tool_func = {t.name: t for t in tools}.get(tool_name)
            if tool_func is None:
                result_str = f"Error: tool '{tool_name}' is not available in this phase."
                t_msg = ToolMessage(tool_call_id=tool_call["id"], content=result_str)
                messages.append(t_msg)
                new_messages_for_state.append(t_msg)
                continue
            result = tool_func.invoke(tool_args)
            result_str = str(result)

            if tool_name == "fetch_github_action_logs" and (
                "PENDING" in result_str.upper() or "PERMISSIONS_ERROR" in result_str.upper()
            ):
                logs_still_pending = True

            if tool_name == "request_fix":
                target = str(tool_args.get("target_agent", "")).lower()
                if "arch" in target: reset_architect = True
                if "infra" in target: reset_infra = True
                fix_requested = True
                # Build healing_context from BOTH diagnosis and healing_instructions.
                # diagnosis contains the filename (needed by architect.py for the
                # is_fix_target check); healing_instructions contains the exact fix.
                # Combining both ensures the target file is identifiable AND the
                # instructions are precise.
                try:
                    payload = json.loads(result_str)
                    diagnosis    = payload.get("diagnosis", "")
                    instructions = payload.get("healing_instructions", "")
                    new_chunk = "\n".join(filter(None, [diagnosis, instructions]))
                    # Accumulate — do not overwrite. Multiple request_fix calls in one
                    # turn must all reach the target agent, not just the last one.
                    healing_context = "\n\n---\n\n".join(filter(None, [healing_context, new_chunk]))
                except (json.JSONDecodeError, AttributeError):
                    pass

            t_msg = ToolMessage(tool_call_id=tool_call["id"], content=result_str)
            messages.append(t_msg)
            new_messages_for_state.append(t_msg)

        if fix_requested:
            break

    # 4. FINAL STATE PREPARATION
    output_state = {
        "messages": new_messages_for_state,
        "next_step": "supervisor",
        "last_agent": "medic",
        # Pass healing_instructions directly to the next agent's system prompt.
        # Empty string when no fix was requested (verification path).
        "healing_context": healing_context,
    }


    # Apply resets and critically UPDATE infra_status
    if reset_architect:
        logger.info("Resetting Architect flag.")
        output_state["architect_status"] = "pending"
        output_state["medic_fix_requested"] = True

    if reset_infra:
        logger.info("Resetting Infra status to pending for fix cycle.")
        output_state["infra_status"] = "pending"
        # github_done stays True — infra.py unlocks push_to_github via medic_triggered_fix
        # infra_provisioned stays True — Terraform re-apply only if explicitly needed

    # If logs are pending the CI run is still in progress, back off before re-routing.
    # attempt is persisted in state so the counter survives across medic re-entries.
    if logs_still_pending and not reset_infra and not reset_architect:
        attempt = state.get("ci_poll_attempt", 0)

        if attempt >= 5:
            # Exceeded ~10 minutes of cumulative waiting — stop polling, surface to user.
            logger.warning("CI run has been pending for over 10 minutes. Stopping poll, routing to supervisor.")
            new_messages_for_state.append(
                HumanMessage(content=(
                    "CI run has been pending for over 10 minutes with no result. "
                    "Possible causes: workflow file not pushed, wrong trigger branch, or GHA disabled. "
                    "Please check GitHub Actions manually."
                ))
            )
            output_state["messages"] = new_messages_for_state
            output_state["ci_poll_attempt"] = 0  # reset for next pipeline run
            output_state["next_step"] = "supervisor"
        else:
            wait = min(30 * (2 ** attempt), _MAX_POLL_WAIT_SECONDS) + random.uniform(0, 5)
            logger.info(f"CI poll attempt {attempt+1}: logs pending. Waiting {wait:.1f}s before retry.")
            time.sleep(wait)
            output_state["ci_poll_attempt"] = attempt + 1
            output_state["next_step"] = "medic"
    else:
        # Not pending — reset the counter so a future CI run starts fresh.
        output_state["ci_poll_attempt"] = 0

    # Store successful verification as institutional knowledge
    if verification_successful:
        try:
            # Build a concise insight from available state
            pipeline_id = state.get("project_id", "unknown")
            cloud = state.get("target_infra", "unknown")
            written = state.get("written_files", [])

            insight_text = (
                f"Pipeline '{pipeline_id}' on '{cloud}' verified successfully. "
                f"Artifacts: {', '.join(written)}. "
                f"No errors detected. CI/CD passed."
            )

            store_architectural_insight.invoke({
                "insight": insight_text,
                "project_id": pipeline_id,
            })
            logger.info("💾 Architectural insight stored after successful verification.")
        except Exception as e:
            logger.warning(f"Failed to store architectural insight: {e}")

    return output_state