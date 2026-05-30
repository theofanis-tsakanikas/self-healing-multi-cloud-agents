import os
import json
import logging
import random
import time
from pathlib import Path
from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, ToolMessage, AIMessage
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


def _extract_validation_summary(messages: list) -> dict[str, tuple[str, str]]:
    """
    Parses state["messages"] and returns a dict mapping filename → (status, detail).
    status is "CLEAN" or "FAILED". detail is the error text for FAILED, empty for CLEAN.

    Strategy: AIMessage.tool_calls links each validate_generated_code call to its filename.
    The matching ToolMessage (by tool_call_id) holds the result text.
    Only the MOST RECENT result per file is kept — earlier attempts are superseded.
    """
    # Build tool_call_id → filename map from AIMessages
    call_id_to_file: dict[str, str] = {}
    for msg in messages:
        if isinstance(msg, AIMessage) and msg.tool_calls:
            for tc in msg.tool_calls:
                if tc.get("name") == "validate_generated_code":
                    fname = tc.get("args", {}).get("filename", "")
                    if fname and tc.get("id"):
                        call_id_to_file[tc["id"]] = fname

    # Walk ToolMessages and classify results; later entries overwrite earlier ones
    results: dict[str, tuple[str, str]] = {}
    for msg in messages:
        if not isinstance(msg, ToolMessage):
            continue
        fname = call_id_to_file.get(msg.tool_call_id)
        if not fname:
            continue
        content = msg.content if isinstance(msg.content, str) else str(msg.content)
        if "VALIDATION FAILED" in content:
            results[fname] = ("FAILED", content)
        elif content.startswith("CLEAN"):
            results[fname] = ("CLEAN", "")

    return results

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

    # 2. VALIDATION SUMMARY — parsed at the Python layer, never left to the LLM to scan messages.
    # The LLM sees a structured list of exactly which files FAILED (with verbatim error text)
    # and which are CLEAN. This is the authoritative source for request_fix decisions:
    # only files in the FAILED list may be passed to request_fix.
    _validation_results = _extract_validation_summary(state["messages"])
    if _validation_results:
        _failed = {f: det for f, (st, det) in _validation_results.items() if st == "FAILED"}
        _clean  = [f for f, (st, _) in _validation_results.items() if st == "CLEAN"]
        _summary_lines = ["\n\n## VALIDATION SUMMARY (authoritative — do not override with your own analysis)"]
        if _failed:
            _summary_lines.append("\n### FAILED — call request_fix ONLY for these files:")
            for fname, detail in _failed.items():
                _summary_lines.append(f"\n**{fname}**\n```\n{detail}\n```")
        else:
            _summary_lines.append("\n### FAILED — none")
        if _clean:
            _summary_lines.append("\n### CLEAN — do NOT call request_fix for these files:")
            _summary_lines.append(", ".join(f"`{f}`" for f in _clean))
        system_prompt += "\n".join(_summary_lines)
        logger.info(
            f"📊 Validation summary injected — FAILED: {list(_failed.keys())}, CLEAN: {_clean}"
        )

    # 3. CONTEXT PREPARATION
    # Smart standard injection: identify WHICH standard is relevant to the current error,
    # then inject only that one from collected_specs (already loaded by architect/infra).
    # This avoids context bloat from dumping all standards AND avoids redundant Pinecone queries.
    # dynamic-experience (past fixes) is never pre-loaded — query_vector_store stays available for it.
    _STANDARD_INDICATORS = {
        "arch_standard_python":   ["scripts/", "pandas", "chunk", "cloud_get", "to_parquet",
                                   "is_suspicious", "destination_uri", "storage_options"],
        "arch_standard_trino":    ["setup_trino.sql", "sql/", "create table", "sync_partition",
                                   "external_location", "partitioned_by"],
        "arch_standard_grafana":  ["dashboards/", "monitoring_specs", "grafana", "timeseries", "uid"],
        "infra_standard_k8s":     ["k8s/", "job.yaml", "configmap", "trino_deployment",
                                   "prometheus_deployment", "grafana_deployment", "00_namespaces",
                                   "initcontainer", "serviceaccountname", "hive-catalog-config",
                                   "pushgateway", "namespace: analytics"],
        "infra_standard_iac":     [".tf", "aws_s3", "iam_policy", "glue", "terraform",
                                   "providers.tf", "main.tf"],
        "infra_standard_dockerfile": ["dockerfile", "docker build", "copy utils", "appuser"],
        "infra_standard_cicd":    [".github/", "workflow", "github_action", "pipeline.yml",
                                   "ecr", "kubectl apply"],
    }

    collected_specs = state.get("collected_specs", {})
    # Build error_context from the structured validation summary + error_log.
    # Using the parsed summary (not raw messages) prevents the LLM from "discovering"
    # errors in CLEAN files via context bleed.
    _failed_text = " ".join(det for _, det in _validation_results.values() if det)
    error_context = (state.get("error_log", "") + " " + _failed_text).lower()

    matched_standards = {}
    for key, indicators in _STANDARD_INDICATORS.items():
        if key in collected_specs and any(ind in error_context for ind in indicators):
            matched_standards[key] = collected_specs[key]

    if matched_standards:
        specs_block = "\n\n**RELEVANT ENGINEERING STANDARD(S) — use directly, do NOT re-query via query_vector_store:**\n"
        for key, content in matched_standards.items():
            specs_block += f"\n### {key}\n{content}\n"
        specs_block += "\nFor past fixes on similar errors, call query_vector_store once (dynamic-experience namespace only)."
        system_prompt += specs_block
        logger.info(f"📋 Injected standards into medic prompt: {list(matched_standards.keys())}")

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