import os
import re
import json
import hashlib
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
    store_architectural_insight,
    _EVIDENCE_MARKERS,
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
_MAX_POLL_ATTEMPTS = 5        # stop after ~10 min cumulative instead of looping forever


def _poll_backoff_seconds(attempt: int) -> int:
    """Deterministic exponential back-off (no jitter) for CI-log polling:
    attempt 0→30, 1→60, 2→120, 3→240, then capped at 300s. Extracted as a pure
    function so the back-off sequence is unit-testable without actually sleeping."""
    return min(30 * (2 ** attempt), _MAX_POLL_WAIT_SECONDS)


def _should_stop_polling(attempt: int) -> bool:
    """True once the poll budget is exhausted — surface to the user instead of
    looping to the graph recursion limit."""
    return attempt >= _MAX_POLL_ATTEMPTS


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

def _owner_of_file(path: str) -> str:
    """Deterministic file → owning-agent mapping.

    The Architect owns the data-plane artifacts (pipeline script, Trino DDL, Grafana
    dashboard, requirements). Infra owns everything deployment-related (k8s manifests,
    Dockerfile, CI workflow, Terraform). A single validation failure belongs to exactly
    ONE of them — never both. Used to override the LLM's free-form target_agent, which
    has mis-routed the SAME .py error to architect AND infra and triggered a cascade
    (infra patching files it does not own, then regenerating unrelated workflows).
    """
    p = path.lower()
    if (p.endswith(".py") or p.endswith(".sql")
            or "dashboards/" in p or p.endswith("requirements.txt")):
        return "architect"
    return "infra"


_AUTOVAL_FAIL_RE = re.compile(r"AUTO-VALIDATION FAILED — fix these errors and rewrite '([^']+)'")


def _latest_autovalidation_failure(messages: list) -> str:
    """Return the file most recently flagged FAILED by the architect's auto-validation.

    The architect runs validate_generated_code in Python (NOT as an LLM tool call) and
    appends "AUTO-VALIDATION FAILED — fix these errors and rewrite '<file>'" to the
    write_project_file ToolMessage. So these failures never appear in
    _extract_validation_summary (which only sees validate_generated_code tool calls).
    Scanning the messages for that marker is the reliable way to recover the failed
    filename for STORAGE/business-rule errors that carry no path of their own. Iterating
    in order and overwriting yields the MOST RECENT failure. Returns "" if none.
    """
    latest = ""
    for msg in messages:
        content = getattr(msg, "content", "") or ""
        if not isinstance(content, str):
            content = str(content)
        for m in _AUTOVAL_FAIL_RE.finditer(content):
            latest = m.group(1).strip()
    return latest


def _accumulate_healing_context(existing: str, new_chunk: str) -> str:
    """Append a new fix chunk to the running healing_context WITHOUT overwriting.
    Multiple request_fix calls in one medic turn must ALL reach the target agent, so
    chunks are joined with a separator (blank entries dropped), never replaced.
    Extracted as a pure function so the accumulate-not-overwrite invariant is testable."""
    return "\n\n---\n\n".join(filter(None, [existing, new_chunk]))


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
        # Single source of truth for the evidence markers: tools._EVIDENCE_MARKERS (the exact list
        # request_fix enforces). Injected here so the prompt can never drift from the tool again.
        _evidence_markers = ", ".join(f"`{m}`" for m in _EVIDENCE_MARKERS)
        system_prompt = format_prompt(
            raw_template,
            project_id=project_id,
            target_infra=target_infra,
            evidence_markers=_evidence_markers,
        )
        
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
    except Exception:
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

    # 2b. TERRAFORM STATE-LOCK GUARD — operational issue, not a code bug.
    # A locked tfstate blob cannot be resolved by any artifact change, so request_fix
    # would loop forever on no-op patches. Detect the marker execute_terraform emits and
    # short-circuit BEFORE the LLM runs: surface actionable guidance and FINISH (via
    # fix_loop_escalated, which the Supervisor routes to FINISH).
    _recent_blob = " ".join(str(getattr(m, "content", "")) for m in state["messages"][-8:])
    if "STATE_LOCK_ERROR" in _recent_blob:
        logger.warning("Terraform STATE_LOCK_ERROR detected — operational, surfacing to user (no fix loop).")
        _lock_msg = (
            "Terraform could not acquire the state lock — the tfstate blob is locked by a "
            "previous (cancelled/killed) run, so NO code fix applies. Break the stale lease "
            "and re-run:\n"
            "  • Azure CLI: az storage blob lease break --account-name <tfstate-account> "
            "--container-name tfstate --blob-name <state-key>\n"
            "  • or Azure Portal → the tfstate blob → Break lease\n"
            "  • or, from terraform/: terraform force-unlock <LOCK_ID>"
        )
        return {
            "messages": [HumanMessage(content=_lock_msg)],
            "next_step": "supervisor",
            "last_agent": "medic",
            "healing_context": "",
            "fix_loop_escalated": True,   # Supervisor → FINISH; do not re-route a doomed fix
            "fix_attempt": 0,
            "last_fix_signature": "",
            "medic_fix_target": "",
        }

    # 3. REASONING LOOP
    fix_requested = False
    fix_signature_parts = []  # error text of each request_fix this turn → loop-convergence signature
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

            if tool_name == "request_fix" and '"REJECTED_BY_MEDIC"' in result_str:
                # Only ACCEPTED fix requests set routing flags. The request_fix tool rejects
                # calls whose evidence_quote carries no error marker (status TOOL_ERROR) —
                # e.g. the LLM hallucinating a "fix" for a CLEAN file, passing evidence
                # "AUTO-VALIDATION: CLEAN ✓". Honouring those would wrongly activate an agent
                # and pollute the convergence signature. Rejected calls fall through: the
                # ToolMessage is still appended below so the LLM sees the rejection and
                # self-corrects.
                target = str(tool_args.get("target_agent", "")).lower()
                if "arch" in target:
                    reset_architect = True
                if "infra" in target:
                    reset_infra = True
                fix_requested = True
                # Capture the error signature (what is being fixed) so the convergence
                # guard below can tell a recurring identical failure (oscillation) apart
                # from progress through different errors.
                fix_signature_parts.append(
                    str(tool_args.get("issue_description", "")
                        or tool_args.get("evidence_quote", "")).strip().lower()
                )
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
                    healing_context = _accumulate_healing_context(healing_context, new_chunk)
                except (json.JSONDecodeError, AttributeError):
                    pass

            t_msg = ToolMessage(tool_call_id=tool_call["id"], content=result_str)
            messages.append(t_msg)
            new_messages_for_state.append(t_msg)

        if fix_requested:
            break

    # 3b. DETERMINISTIC OWNERSHIP ROUTING — override the LLM's target_agent.
    # A file-validation failure belongs to exactly ONE agent. The LLM has mis-routed
    # the SAME .py error to BOTH architect and infra; honouring that drags infra into
    # patching files it doesn't own and regenerating unrelated artifacts (broken CI
    # workflow pushed to the repo). Derive the reset flags from the AUTHORITATIVE
    # FAILED-file list instead of trusting the LLM. (Skipped for CI-log failures,
    # which produce no validate_generated_code results — those keep the LLM's target.)
    deterministic_fix_target = ""  # written to state so the Supervisor routes by ownership
    if fix_requested:
        _failed_files = [f for f, (st, _) in _validation_results.items() if st == "FAILED"]
        if not _failed_files:
            # Architect auto-validation failures never reach _validation_results (validate
            # runs in Python, not as an LLM tool call). Recover the SINGLE most-recently
            # failed file from the messages so the architect patches EXACTLY that file —
            # not every artifact it generated. (Prevents over-patching a correct SQL/dashboard.)
            _latest_failed = _latest_autovalidation_failure(state["messages"])
            _failed_files = [_latest_failed] if _latest_failed else []
        if _failed_files:
            _owners = {_owner_of_file(f) for f in _failed_files}
            reset_architect = "architect" in _owners
            reset_infra = "infra" in _owners
            # Architect first when both fail — fix the data plane before redeploying infra.
            deterministic_fix_target = "architect" if "architect" in _owners else "infra"
            logger.info(
                f"🧭 Ownership routing: FAILED {_failed_files} → {sorted(_owners)} "
                f"(target={deterministic_fix_target})"
            )
            # Ensure the FAILED filename(s) appear in healing_context. The architect's
            # is_patch_target check (architect.py) requires the file stem to be present in
            # the diagnosis, but custom-validator errors (e.g. "STORAGE: storage_options")
            # carry NO file path — unlike ruff errors which include "--> scripts/...py".
            # Without this the architect rejects its OWN patch ("not the fix target") and
            # the fix loops. Prepend the names only when none is already mentioned.
            if healing_context and not any(
                Path(f).stem.lower() in healing_context.lower() for f in _failed_files
            ):
                healing_context = (
                    "Target file(s) to fix: " + ", ".join(_failed_files) + "\n\n" + healing_context
                )
                logger.info("🩹 Injected FAILED filename(s) into healing_context for patch targeting.")

    # 4. FINAL STATE PREPARATION
    output_state = {
        "messages": new_messages_for_state,
        "next_step": "supervisor",
        "last_agent": "medic",
        # Pass healing_instructions directly to the next agent's system prompt.
        # Empty string when no fix was requested (verification path).
        "healing_context": healing_context,
        # Deterministic ownership target — the Supervisor honours this over re-parsing
        # the request_fix message (which may name the wrong/both agents). "" = fall back
        # to message parsing (CI-log failures have no validation result to map).
        "medic_fix_target": deterministic_fix_target,
    }


    # ── FIX-LOOP CONVERGENCE GUARD ───────────────────────────────────────────
    # A fix that never validates — e.g. an SQL column-reorder the architect's
    # surgical patch keeps DUPLICATING instead of moving — would otherwise bounce
    # medic → architect → medic until the graph recursion_limit (200), forcing a
    # manual stop. Detect a fix request whose error signature is IDENTICAL to the
    # previous round (no progress) and, after _MAX_FIX_ATTEMPTS repeats, stop and
    # surface to the user instead of looping. A DIFFERENT signature = real progress
    # through distinct errors, so the counter restarts (legit multi-fix sequences
    # are never penalised). Mirrors the ci_poll_attempt back-off pattern.
    _MAX_FIX_ATTEMPTS = 3
    escalated_fix_loop = False
    if fix_requested:
        current_sig = hashlib.sha256(
            "||".join(sorted(filter(None, fix_signature_parts))).encode()
        ).hexdigest()
        if current_sig and current_sig == state.get("last_fix_signature", ""):
            fix_attempt = state.get("fix_attempt", 0) + 1  # same error again → not converging
        else:
            fix_attempt = 1  # new/different error → progress, restart the count

        if fix_attempt >= _MAX_FIX_ATTEMPTS:
            logger.warning(
                f"Fix loop not converging: the same error survived {fix_attempt} "
                f"fix attempts. Stopping self-heal and surfacing to the user."
            )
            new_messages_for_state.append(HumanMessage(content=(
                f"Self-healing could not resolve this after {fix_attempt} attempts — the same "
                f"validation error keeps recurring, so the automated fix is not converging "
                f"(the surgical patch cannot resolve it; a manual edit to the standard/prompt "
                f"is likely needed). Last diagnosis:\n\n{healing_context}"
            )))
            output_state["messages"] = new_messages_for_state
            output_state["next_step"] = "supervisor"
            output_state["fix_attempt"] = 0
            output_state["last_fix_signature"] = ""
            output_state["healing_context"] = ""  # do NOT route the doomed fix to an agent
            output_state["medic_fix_target"] = ""  # cancel any pending ownership route
            # Explicit flag the supervisor honours — without it, supervisor RULE C would
            # re-derive the fix target from the request_fix message still in history and
            # route back to the architect, defeating this guard.
            output_state["fix_loop_escalated"] = True
            escalated_fix_loop = True
        else:
            output_state["fix_attempt"] = fix_attempt
            output_state["last_fix_signature"] = current_sig
    elif verification_successful:
        # Clean verification — reset so the next pipeline's fix cycle starts fresh.
        output_state["fix_attempt"] = 0
        output_state["last_fix_signature"] = ""

    # Apply resets and critically UPDATE infra_status (skipped when the loop guard
    # escalated — we must NOT re-route the non-converging fix back to an agent).
    if reset_architect and not escalated_fix_loop:
        logger.info("Resetting Architect flag.")
        output_state["architect_status"] = "pending"
        output_state["medic_fix_requested"] = True

    if reset_infra and not escalated_fix_loop:
        logger.info("Resetting Infra status to pending for fix cycle.")
        output_state["infra_status"] = "pending"
        # github_done stays True — infra.py unlocks push_to_github via medic_triggered_fix
        # infra_provisioned stays True — Terraform re-apply only if explicitly needed

    # If logs are pending the CI run is still in progress, back off before re-routing.
    # attempt is persisted in state so the counter survives across medic re-entries.
    if logs_still_pending and not reset_infra and not reset_architect:
        attempt = state.get("ci_poll_attempt", 0)

        if _should_stop_polling(attempt):
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
            wait = _poll_backoff_seconds(attempt) + random.uniform(0, 5)
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