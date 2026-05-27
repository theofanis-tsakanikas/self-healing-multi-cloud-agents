import os
import logging
import json
from pathlib import Path
from dotenv import load_dotenv
from langchain_core.messages import ToolMessage
from agents.llm_factory import get_llm

# 1. INTERNAL IMPORTS
from agents.state import AgentState
from agents.constants import (
    ARCHITECT_PROMPT_FILE,
    DEFAULT_REQUIRED_ARTIFACTS,
    PROMPTS_DIR,
    TEMPERATURE,
)
from utils.prompt_utils import format_prompt
from utils.file_utils import read_file
from utils.message_utils import safe_recent_messages
from utils.config_utils import build_architect_context
from agents.tools import read_data_schema, write_project_file, query_vector_store, validate_generated_code

# Logger configuration
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("ARCHITECT")

load_dotenv()

def _normalize_filename(filename: str) -> str:
    """Normalize file paths for consistent policy checks."""
    return (filename or "").replace("\\", "/").strip()

def _is_architect_allowed_file(filename: str) -> bool:
    """
    Security Filter: Checks if the Architect is permitted to write the file.
    Architects are restricted to logic, metadata, and data artifacts.
    """
    normalized = _normalize_filename(filename)
    if not normalized:
        return False

    lower_name = Path(normalized).name.lower()
    lower_path = normalized.lower()
    ext = Path(normalized).suffix.lower()

    # Allowed: Scripts, SQL, Metadata, Docs
    if lower_name == "requirements.txt":
        return True
    if ext in {".py", ".sql", ".json", ".csv", ".md"}:
        return True

    # Blocked: Infrastructure and Core Configuration
    blocked_names = {"dockerfile", "docker-compose.yml", "docker-compose.yaml"}
    blocked_exts = {".tf", ".tfvars", ".yaml", ".yml"}
    
    if lower_name in blocked_names or ext in blocked_exts:
        return False
    
    # Path-based blocking for protected directories
    if any(p in lower_path for p in ["/k8s/", "k8s/", "/terraform/", "terraform/"]):
        return False

    return True


def _resolve_artifacts(pipe_conf: dict, written_files: list[str]) -> list[str]:
    """
    Returns list of artifact paths still missing from written_files.
    Required artifacts come from pipe_conf['required_artifacts'] if present,
    otherwise falls back to DEFAULT_REQUIRED_ARTIFACTS from constants.
    Supports {pipeline_name} placeholder — resolved from pipe_conf.
    """
    required = pipe_conf.get("required_artifacts", DEFAULT_REQUIRED_ARTIFACTS)
    pipeline_name = pipe_conf.get("pipeline_id", "pipeline").lower()
    resolved_required = [
        artifact.format(pipeline_name=pipeline_name)
        for artifact in required
    ]

    written_names = [Path(f).name.lower() for f in written_files]
    all_extensions = {os.path.splitext(f)[1].lower() for f in written_files}

    missing = []
    for artifact in resolved_required:
        if artifact.endswith(".py"):
            if ".py" not in all_extensions:
                missing.append(artifact)
        elif Path(artifact).name.lower() not in written_names:
            missing.append(artifact)
    return missing


def architect_node(state: AgentState):
    """
    Architect node: Handles pipeline logic design using a Phase-Gate approach.
    Phase 1: Discovery (Vector Store lookup only).
    Phase 2: Implementation (Writing code/DDL based on retrieved standards).
    """
    logger.info("--- STARTING ARCHITECT NODE ---")
    
    llm = get_llm(temperature=TEMPERATURE)

    # 1. INITIALIZE TRACKING & KNOWLEDGE STORAGE
    written_files = list(state.get("written_files", []))
    # Retrieve existing specs from state or initialize a new dictionary
    collected_specs = dict(state.get("collected_specs", {})) 

    # 2. CONTEXT EXTRACTION
    project_id = state.get("project_id", "Unknown")
    raw_configs = state.get("raw_configs", {})
    
    pipe_conf = raw_configs.get("pipeline", {})
    db_conf = raw_configs.get("database", {})
    rules_conf = raw_configs.get("rules", {})
    infra_conf = raw_configs.get("infrastructure", {})

    # 3. CONTEXT GENERATION
    try:
        architect_context = build_architect_context(pipe_conf, db_conf, rules_conf, infra_conf)
        target_infra_name = infra_conf.get('service_name', 'generic-service')
    except Exception as e:
        logger.error(f"Critical Context Error: {str(e)}")
        architect_context = json.dumps({"error": str(e)})
        target_infra_name = "error-state"

    # 4. TOOL BINDING & PHASE-GATE LOGIC
    full_tools_map = {
        "read_data_schema": read_data_schema,
        "write_project_file": write_project_file,
        "query_vector_store": query_vector_store,
        "validate_generated_code": validate_generated_code,
    }

    required_knowledge_keys = ["arch_standard_trino", "arch_standard_grafana", "arch_standard_python"]
    is_fix_mode = state.get("medic_fix_requested", False) or state.get("last_agent") == "medic"
    has_all_standards = all(key in collected_specs for key in required_knowledge_keys)
    schema_discovered = state.get("schema_discovered", False)

    missing_artifacts = []

    # 3-Phase Gate (fix mode bypasses all gates)
    if is_fix_mode:
        if not has_all_standards:
            allowed_tool_names = ["query_vector_store"]
            phase_instruction = "CURRENT PHASE: FIX MODE — DISCOVERY. Standards missing, retrieve them first."
        elif not schema_discovered:
            allowed_tool_names = ["read_data_schema"]
            phase_instruction = "CURRENT PHASE: FIX MODE — SCHEMA. Schema not yet read, retrieve it before writing."
        else:
            allowed_tool_names = ["write_project_file", "query_vector_store"]
            phase_instruction = (
                "CURRENT PHASE: FIX MODE. "
                "The Medic's diagnosis and healing_instructions are in your message history as a REJECTED_BY_MEDIC payload. "
                "Step 1: Call query_vector_store using the error signature from the diagnosis as your query. "
                "Step 2: Rewrite ONLY the affected file using write_project_file. "
                "CRITICAL: If Step 1 returns 'No relevant guidelines found', do NOT stop. "
                "Use the 'healing_instructions' field from the REJECTED_BY_MEDIC payload in your message history — "
                "it contains the exact fix to apply. An empty Knowledge Base result is never a reason to take no action."
            )
        logger.info(f"🔧 GATE: Fix mode. Tools: {allowed_tool_names}")
    elif not has_all_standards:
        # Phase 1 — Discovery: retrieve all engineering standards first
        allowed_tool_names = ["query_vector_store"]
        missing_keys = [k for k in required_knowledge_keys if k not in collected_specs]
        phase_instruction = (
            f"CURRENT PHASE: DISCOVERY. "
            f"Execute query_vector_store for the MISSING standards only: {missing_keys}. "
            "Use the exact query strings defined under 'KNOWLEDGE RETRIEVAL & SPEC EXTRACTION' in your instructions. "
            "Do NOT query for any other topic."
        )
        logger.info(f"⚠️ GATE: Discovery Phase. Missing keys: {missing_keys}")
    elif not schema_discovered:
        # Phase 2 — Schema: read the actual DB table before writing any code
        allowed_tool_names = ["read_data_schema"]
        table_name = db_conf.get("default_table", "")
        phase_instruction = f"CURRENT PHASE: SCHEMA DISCOVERY. Call read_data_schema EXACTLY ONCE with table_name='{table_name}'. Do not call it with any other value."
        logger.info("⚠️ GATE: Schema not yet discovered. Forcing Schema Phase.")
    else:
        # Phase 3 — Implementation: write all artifacts, then validate .py files
        allowed_tool_names = ["write_project_file", "validate_generated_code"]

        # Compute missing artifacts using exact filenames/paths so the LLM
        # passes the correct `filename` argument without ambiguity.
        missing_artifacts = _resolve_artifacts(pipe_conf, written_files)

        if missing_artifacts:
            phase_instruction = (
                f"CURRENT PHASE: IMPLEMENTATION. "
                f"Call write_project_file for each of these missing files "
                f"(use the path as the exact filename argument): {', '.join(missing_artifacts)}. "
                "Do NOT write any file not listed here."
            )
        else:
            phase_instruction = "CURRENT PHASE: IMPLEMENTATION. All artifacts written. Return status."
        logger.info(f"✅ GATE: Implementation phase. Missing: {missing_artifacts or 'none'}")

    # Bind only the phase-specific tools to the LLM.
    # Force tool_choice="required" in every phase where there is still work to do —
    # without this the LLM can skip the tool call and reply with plain text,
    # which triggers the Supervisor's keyword scan and causes false Medic routing.
    current_phase_tools = [full_tools_map[name] for name in allowed_tool_names]

    should_force_tool = not is_fix_mode and (
        # Discovery phase: standards still missing
        ("query_vector_store" in allowed_tool_names and not has_all_standards)
        # Schema phase: schema not yet read
        or ("read_data_schema" in allowed_tool_names and not schema_discovered)
        # Implementation phase: artifacts still missing
        or ("write_project_file" in allowed_tool_names and bool(missing_artifacts))
    )

    if should_force_tool:
        llm_with_tools = llm.bind_tools(current_phase_tools, tool_choice="required")
    else:
        llm_with_tools = llm.bind_tools(current_phase_tools)

    # 5. PROMPT ORCHESTRATION
    prompt_path = os.path.join(PROMPTS_DIR, ARCHITECT_PROMPT_FILE)
    raw_template = read_file(prompt_path)
    system_prompt = format_prompt(
        raw_template, 
        architect_context=architect_context, 
        project_id=project_id
    )
    
    # Add dynamic instructions regarding phase and knowledge capture
    system_prompt += f"\n\nCRITICAL: {phase_instruction}"
    system_prompt += "\nNote: Summarize all retrieved standards into the required state keys."

    # Inject standards into the system prompt for the implementation phase.
    # safe_recent_messages trims discovery ToolMessages out of context by the time
    # write_project_file is available — without this the LLM writes from general knowledge.
    if has_all_standards and "write_project_file" in allowed_tool_names:
        needs_python = any(a.endswith(".py") for a in missing_artifacts)
        needs_sql = any(a.endswith(".sql") for a in missing_artifacts)
        needs_grafana = any(a.endswith(".json") for a in missing_artifacts)

        system_prompt += "\n\n## ENGINEERING STANDARDS — follow these exactly:\n"
        if needs_python and "arch_standard_python" in collected_specs:
            system_prompt += f"\n### arch_standard_python\n{collected_specs['arch_standard_python']}\n"
        if needs_sql and "arch_standard_trino" in collected_specs:
            system_prompt += f"\n### arch_standard_trino\n{collected_specs['arch_standard_trino']}\n"
        if needs_grafana and "arch_standard_grafana" in collected_specs:
            system_prompt += f"\n### arch_standard_grafana\n{collected_specs['arch_standard_grafana']}\n"

    # 6. PREPARE CONVERSATION
    recent_messages = safe_recent_messages(state["messages"])
    messages = [
        {"role": "system", "content": system_prompt}
    ] + recent_messages + [
        {"role": "user", "content": state.get("task", "Execute the current phase of the pipeline design.")}
    ]

    # 7. LLM CALL & TOOL LOOP
    # Up to 3 iterations so the LLM can self-correct when it skips already-written files
    # instead of writing the one still missing (e.g. requirements.txt).
    any_tool_error = False
    last_generated_code = state.get("generated_code", "")
    new_messages = []

    for _iter in range(3):
        response = llm_with_tools.invoke(messages)
        new_messages.append(response)
        messages.append(response)

        if not response.tool_calls:
            break

        all_skipped_this_iter = True  # Tracks whether every write was a no-op skip

        for tool_call in response.tool_calls:
            tool_name = tool_call["name"]
            tool_args = tool_call["args"]

            # Verify if the LLM attempted to use an allowed tool for the current phase
            if tool_name in allowed_tool_names:
                tool_func = full_tools_map[tool_name]
                try:
                    # Logic for Knowledge Capture (Discovery Phase)
                    if tool_name == "query_vector_store":
                        result = tool_func.invoke(tool_args)
                        res_lower = str(result).lower()
                        all_skipped_this_iter = False

                        # "Catch-All" strategy: scan the response content
                        # Regardless of what was asked, if the response contains the knowledge, we store it.

                        # 1. Check for Python Standards
                        if any(word in res_lower for word in [
                            "sqlalchemy", "pandas", "chunksize", "os.getenv",
                            "python", "def ", "import ", "logging", ".py",
                            "etl", "ingestion", "script", "boto3", "pyspark",
                        ]):
                            collected_specs["arch_standard_python"] = str(result)
                            logger.info("🎯 Smart Mapping: Python Standards Secured.")

                        # 2. Check for Trino Standards
                        if any(word in res_lower for word in ["trino", "ddl", "create table", "varchar"]):
                            collected_specs["arch_standard_trino"] = str(result)
                            logger.info("🎯 Smart Mapping: Trino Standards Secured.")

                        # 3. Check for Grafana Standards
                        if any(word in res_lower for word in ["grafana", "schemaversion", "json dashboard"]):
                            collected_specs["arch_standard_grafana"] = str(result)
                            logger.info("🎯 Smart Mapping: Grafana Standards Secured.")

                        # Fallback: if smart mapping still didn't capture a missing key
                        # and the result is non-empty, store it for the first missing key.
                        # Prevents infinite discovery loops when content keywords don't match.
                        no_relevant = "no relevant guidelines found" in res_lower
                        still_missing = [k for k in required_knowledge_keys if k not in collected_specs]
                        if still_missing and not no_relevant and str(result).strip():
                            target_key = still_missing[0]
                            collected_specs[target_key] = str(result)
                            logger.info(f"🎯 Fallback Mapping: {target_key} secured from query result.")

                    # Logic for Writing Files (Implementation Phase)
                    elif tool_name == "write_project_file":
                        filename = tool_args.get("filename", "")
                        if not _is_architect_allowed_file(filename):
                            result = f"Policy Error: Architect is not permitted to modify '{filename}'."
                            any_tool_error = True
                        elif filename in written_files and not is_fix_mode:
                            result = f"Skipped: '{filename}' already exists in workspace."
                            logger.info(f"⏭️ Skipping existing file: {filename}")
                        else:
                            result = tool_func.invoke(tool_args)
                            if filename not in written_files:
                                written_files.append(filename)
                            if filename.endswith(".py"):
                                last_generated_code = tool_args.get("content", "")
                            all_skipped_this_iter = False

                    # Logic for Schema Reading (Phase 2)
                    elif tool_name == "read_data_schema":
                        if not schema_discovered:
                            result = tool_func.invoke(tool_args)
                            schema_discovered = True
                            all_skipped_this_iter = False
                            logger.info("✅ Schema discovered. Unlocking Implementation phase.")
                        else:
                            result = "Skipped: schema already discovered this turn."
                            logger.info("⏭️ Skipping redundant read_data_schema call.")

                except Exception as e:
                    logger.error(f"Tool Failure [{tool_name}]: {e}")
                    result = f"Tool Execution Error: {str(e)}"
                    any_tool_error = True
            else:
                # Security/Logic fallback if the LLM tries to bypass the gate
                result = f"Error: Tool '{tool_name}' is locked in the current phase."
                any_tool_error = True

            t_msg = ToolMessage(tool_call_id=tool_call["id"], content=str(result))
            new_messages.append(t_msg)
            messages.append(t_msg)

        # After processing all tool calls, recompute what is still missing.
        # If the LLM only rewrote existing files (all_skipped), inject a correction
        # so the next iteration focuses exclusively on the remaining files.
        if "write_project_file" in allowed_tool_names:
            still_missing = _resolve_artifacts(pipe_conf, written_files)

            if not still_missing:
                break  # All artifacts written — exit the loop early

            if all_skipped_this_iter and _iter < 2:
                correction = (
                    f"CORRECTION: Every file you attempted already exists and was skipped. "
                    f"These files are STILL MISSING and MUST be written now: {', '.join(still_missing)}. "
                    f"Do NOT rewrite existing files. Call write_project_file for each missing file immediately."
                )
                messages.append({"role": "user", "content": correction})
                logger.info(f"🔄 Correction injected (iter {_iter}). Still missing: {still_missing}")
        else:
            break  # Not in implementation phase — one iteration is enough

    # 8. STATUS UPDATE & RETURN
    still_missing_final = _resolve_artifacts(pipe_conf, written_files)
    status = "completed" if (
        not still_missing_final
        and has_all_standards
        and schema_discovered
        and not any_tool_error
    ) else "pending"

    return {
        "messages": new_messages,
        "written_files": written_files,
        "generated_code": last_generated_code,
        "collected_specs": collected_specs,
        "schema_discovered": schema_discovered,
        "target_infra": target_infra_name,
        "architect_status": status,
        "next_step": "supervisor",
        "last_agent": "architect",
        "agent_error": any_tool_error,
    }