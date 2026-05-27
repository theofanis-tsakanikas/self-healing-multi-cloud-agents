import os
import re
import logging
import json
from pathlib import Path
from langchain_core.messages import ToolMessage, HumanMessage
from agents.llm_factory import get_llm
from agents.state import AgentState
from utils.prompt_utils import format_prompt
from utils.file_utils import read_file
from utils.message_utils import safe_recent_messages
from utils.config_utils import build_infra_context, build_databricks_infra_context

# Import infrastructure automation tools
from agents.tools import (
    write_terraform_config, execute_terraform, generate_dockerfile,
    generate_k8s_manifest, generate_github_action, push_to_github, query_vector_store
)

from agents.constants import (
    DEFAULT_REQUIRED_DATABRICKS_TF_FILES,
    DEFAULT_REQUIRED_K8S_MANIFESTS,
    INFRA_PROMPT_FILE,
    PROMPTS_DIR,
    TEMPERATURE,
)

logger = logging.getLogger("INFRA")

def files_exist_in_state(target_files: list, written_files: list) -> bool:
    """
    Checks if target_files are present in written_files.
    Uses lower-case comparison for cross-platform reliability.
    """
    if not target_files: return False
    written_files_lower = {f.lower() for f in written_files}
    return set(f.lower() for f in target_files).issubset(written_files_lower)

def infra_node(state: AgentState):
    """
    Infrastructure agent node managing Terraform, Containerization, and CI/CD.
    
    Implements a 3-Phase Gate System:
    1. Discovery Phase: Only query_vector_store is available until standards are retrieved.
    2. Implementation Phase: Unlocks IaC, K8s, and Docker tools progressively.
    3. Action Lock: Permanently removes execution tools (Docker/Push) once SUCCESS is detected.
    """
    logger.info("--- STARTING INFRASTRUCTURE NODE ---")
    llm = get_llm(temperature=TEMPERATURE)
    
    # 1. LOCAL STATE EXTRACTION
    written_files = state.get("written_files", [])
    tf_done = state.get("infra_provisioned", False)
    medic_triggered_fix = state.get("medic_fix_requested", False) or (state.get("last_agent") == "medic")
    project_id = state.get("project_id")
    collected_specs = dict(state.get("collected_specs", {})) 
    
    # 2. CONTEXT GENERATION
    raw_configs = state.get("raw_configs", {})
    pipeline_conf = raw_configs.get("pipeline", {})
    infra_conf = raw_configs.get("infrastructure", {})
    
    provider = infra_conf.get("provider", "kubernetes").lower()
    is_databricks = provider == "databricks"

    try:
        if is_databricks:
            infra_context = build_databricks_infra_context(pipeline_conf, infra_conf)
            logger.info("🧱 Databricks provider detected. Using Databricks context.")
        else:
            infra_context = build_infra_context(pipeline_conf, infra_conf)
    except Exception as e:
        logger.error(f"Failed to build infra context: {e}")
        infra_context = json.dumps({"error": str(e)})

    # 3. FULL TOOLSET DEFINITION
    full_tools_map = {
        "write_terraform_config": write_terraform_config,
        "execute_terraform": execute_terraform,
        "generate_dockerfile": generate_dockerfile,
        "generate_k8s_manifest": generate_k8s_manifest,
        "generate_github_action": generate_github_action,
        "push_to_github": push_to_github,
        "query_vector_store": query_vector_store
    }

    # 4. PHASE-GATE LOGIC (PROGRESSIVE TOOL LOCKING)
    # infra_standard_service_account is NOT a separate required key —
    # k8s_deployment_rules.md (Section 8) already contains the cloud-specific
    # ServiceAccount / IRSA / Workload Identity spec inside infra_standard_k8s.
    required_standards = ["infra_standard_iac", "infra_standard_k8s", "infra_standard_cicd", "infra_standard_dockerfile"]
    has_all_standards = all(key in collected_specs for key in required_standards)

    selected_keys = []
    orchestration_phase_instruction = None

    # --- GATE 1: KNOWLEDGE DISCOVERY ---
    if not has_all_standards:
        # Agent is forced to retrieve engineering standards before writing any code
        selected_keys = ["query_vector_store"]
        logger.info("🛡️ GATE: Discovery Phase. Only query_vector_store allowed.")
    
    # --- GATE 2: DATABRICKS ---
    elif is_databricks:
        logger.info("✅ GATE: Implementation Phase. Standards verified.")

        # Databricks flow: Terraform + CI/CD only (no K8s, no Dockerfile)
        tf_required = DEFAULT_REQUIRED_DATABRICKS_TF_FILES
        github_action_ready = any(
            ".github/workflows" in f.lower() for f in written_files
        )

        if not tf_done:
            if not files_exist_in_state(tf_required, written_files) or medic_triggered_fix:
                selected_keys = ["write_terraform_config", "execute_terraform"]
            else:
                selected_keys = ["execute_terraform"]
            logger.info("🧱 Databricks GATE: Terraform phase.")

        elif not github_action_ready or medic_triggered_fix:
            selected_keys = ["generate_github_action"]
            orchestration_phase_instruction = (
                "CURRENT OPERATIONAL PHASE: DATABRICKS CI/CD. "
                "Generate the GitHub Actions workflow for Databricks deployment. "
                "Use databricks-cli for authentication and job execution. "
                "Do NOT generate Dockerfile or K8s manifests — "
                "Databricks manages its own compute."
            )
            logger.info("🧱 Databricks GATE: CI/CD phase.")

        else:
            if not state.get("github_done", False) or medic_triggered_fix:
                selected_keys = ["push_to_github"]
            logger.info("🧱 Databricks GATE: Push phase.")

    # --- GATE 2: INFRASTRUCTURE IMPLEMENTATION ---
    else:
        logger.info("✅ GATE: Implementation Phase. Standards verified.")

        # Step A: Terraform / IaC
        if not tf_done:
            tf_required = ["terraform/providers.tf", "terraform/main.tf", "terraform/variables.tf", "terraform/outputs.tf", "terraform/terraform.tfvars"]
            if not files_exist_in_state(tf_required, written_files) or medic_triggered_fix:
                selected_keys = ["write_terraform_config", "execute_terraform"]
            else:
                selected_keys = ["execute_terraform"]

        # Step B: Orchestration & CI/CD
        else:
            k8s_required = infra_conf.get(
                "required_k8s_manifests",
                DEFAULT_REQUIRED_K8S_MANIFESTS,
            )
            k8s_ready = files_exist_in_state(k8s_required, written_files)
            docker_ready = any(f.lower().endswith("dockerfile") for f in written_files)
            github_ready = any(".github/workflows" in f.lower() for f in written_files)

            # If the Architect handled the fix (medic_fix_requested=True), skip regeneration —
            # the Architect already wrote the fix file, Infra just needs to push it.
            # Direct Medic→Infra fixes (last_agent=="medic") still go through file generation.
            if state.get("medic_fix_requested", False) and state.get("github_done", False):
                selected_keys = ["push_to_github"]

            elif not (k8s_ready and docker_ready and github_ready) or medic_triggered_fix:
                # Compute exactly which files are missing so the LLM doesn't regenerate
                # files that are already tracked — avoids the re-generation loop.
                missing_orchestration = []
                if not docker_ready:
                    missing_orchestration.append("Dockerfile")
                for f in k8s_required:
                    written_lower = {w.lower() for w in written_files}
                    if f.lower() not in written_lower:
                        missing_orchestration.append(f)
                if k8s_ready and docker_ready and not github_ready:
                    missing_orchestration.append(".github/workflows/<project_id>_pipeline.yaml")

                orchestration_phase_instruction = (
                    f"CURRENT OPERATIONAL PHASE: IMPLEMENTATION — ORCHESTRATION. "
                    f"Generate ONLY these missing files: {missing_orchestration}. "
                    "Do NOT regenerate files that already exist."
                )

                selected_keys = []
                if not docker_ready or medic_triggered_fix:
                    selected_keys.append("generate_dockerfile")
                if not k8s_ready or medic_triggered_fix:
                    selected_keys.append("generate_k8s_manifest")
                # generate_github_action unlocks only after K8s manifests and Dockerfile
                # are fully ready — the workflow references these artifacts.
                if (k8s_ready and docker_ready and not github_ready) or medic_triggered_fix:
                    selected_keys.append("generate_github_action")
            else:
                missing_orchestration = []
                orchestration_phase_instruction = None
                # Provide execution tools ONLY if they haven't succeeded yet (Action Lock)
                # execute_docker_command is intentionally excluded — build/push is handled
                # by the GitHub Actions workflow after push_to_github.
                if not state.get("github_done", False) or medic_triggered_fix:
                    selected_keys.append("push_to_github")

    # --- GATE 3: MEDIC OVERRIDE ---
    # Always re-enable query_vector_store in fix mode. The LLM only sees the last N
    # messages — original ToolMessages with standards are gone from context. Querying
    # with the error signature retrieves both the relevant standard (engineering-standards)
    # and any matching past fix (dynamic-experience).
    # push_to_github must also be available: the agent rewrites the affected file and
    # must push immediately in the same turn — without it the fix is never deployed and
    # the next invocation (last_agent="infra") hits the EARLY EXIT falsely.
    if medic_triggered_fix:
        if "query_vector_store" not in selected_keys:
            selected_keys.append("query_vector_store")
        if "push_to_github" not in selected_keys:
            selected_keys.append("push_to_github")
        logger.info("🔧 MEDIC BYPASS: Re-enabling query_vector_store + push_to_github for error-driven fix.")

    # 5. EARLY EXIT GATE
    # If all files exist, all actions are done, and standards are met, signal completion.
    if not selected_keys and has_all_standards:
        logger.info("🎯 MISSION ACCOMPLISHED: Infrastructure node finalized.")
        return {
            "messages": [HumanMessage(content="INFRA_COMPLETE: Infrastructure and CI/CD are finalized.")],
            "infra_status": "completed",
            "next_step": "supervisor",
            "last_agent": "infra"
        }

    # 6. LLM BINDING (Injecting only allowed tools for the current phase)
    # parallel_tool_calls=False prevents OpenAI from wrapping calls in multi_tool_use.parallel,
    # which LangChain can misparse and corrupt tool arguments with JSON fragments.
    current_tools = {k: v for k, v in full_tools_map.items() if k in selected_keys}

    # Force tool_choice="required" in discovery phase so the LLM cannot skip
    # tool calls and reply with plain text (which triggers false Medic routing).
    if not has_all_standards and not medic_triggered_fix:
        llm_with_tools = llm.bind_tools(list(current_tools.values()), tool_choice="required")
    else:
        llm_with_tools = llm.bind_tools(list(current_tools.values()))

    # 7. PROMPT PREPARATION
    try:
        raw_template = read_file(os.path.join(PROMPTS_DIR, INFRA_PROMPT_FILE))
        system_prompt = format_prompt(raw_template, infra_context=infra_context, project_id=project_id)
    except Exception as e:
        logger.error(f"Prompt formatting failed: {e}")
        system_prompt = "You are an Infrastructure Specialist."

    # Inform the agent about the current operational phase
    if not has_all_standards:
        missing_keys = [k for k in required_standards if k not in collected_specs]
        phase_text = (
            f"CURRENT OPERATIONAL PHASE: DISCOVERY. "
            f"Execute query_vector_store for the MISSING standards only: {missing_keys}. "
            "Use the exact query strings defined under 'KNOWLEDGE RETRIEVAL & MANDATORY ALIGNMENT' in your instructions. "
            "Do NOT query for any other topic."
        )
        logger.info(f"🛡️ GATE: Discovery Phase. Missing keys: {missing_keys}")
    elif medic_triggered_fix:
        phase_text = (
            "CURRENT OPERATIONAL PHASE: FIX MODE. "
            "The Medic's diagnosis and healing_instructions are in your message history as a REJECTED_BY_MEDIC payload. "
            "Step 1: Call query_vector_store using the error signature from the diagnosis as your query. "
            "Step 2: Rewrite ONLY the affected file using the appropriate generation tool "
            "(generate_github_action for CI/CD errors, generate_k8s_manifest for Kubernetes errors, etc.). "
            "Step 3: Immediately call push_to_github to deploy the fix. "
            "CRITICAL RULES — violating any of these is a failure: "
            "(1) If query_vector_store returns 'No relevant guidelines found', do NOT stop — "
            "use the 'healing_instructions' field from the REJECTED_BY_MEDIC payload instead. "
            "(2) You MUST call push_to_github in this same turn after rewriting the file. "
            "Stopping after query_vector_store or after generating the file without pushing is NOT acceptable. "
            "(3) An empty Knowledge Base result is never a reason to take no action."
        )
    else:
        phase_text = orchestration_phase_instruction or "CURRENT OPERATIONAL PHASE: IMPLEMENTATION"

    system_prompt += f"\n\n{phase_text}"

    # Inject only the standards relevant to the current sub-phase.
    # safe_recent_messages(limit=5) trims discovery ToolMessages out of context by the
    # time the implementation phase runs — without this injection the LLM never sees them.
    if has_all_standards:
        if is_databricks:
            if "write_terraform_config" in selected_keys or \
               "execute_terraform" in selected_keys:
                relevant_keys = ["infra_standard_iac"]
            elif "generate_github_action" in selected_keys:
                relevant_keys = ["infra_standard_cicd"]
            else:
                relevant_keys = []
        elif "write_terraform_config" in selected_keys or \
             "execute_terraform" in selected_keys:
            relevant_keys = ["infra_standard_iac"]
        elif any(k in selected_keys for k in [
            "generate_dockerfile", "generate_k8s_manifest", "generate_github_action"
        ]):
            relevant_keys = ["infra_standard_k8s", "infra_standard_dockerfile", "infra_standard_cicd"]
        else:
            relevant_keys = []

        if relevant_keys:
            system_prompt += "\n\n## ENGINEERING STANDARDS — follow these exactly:\n"
            for key in relevant_keys:
                if key in collected_specs:
                    system_prompt += f"\n### {key}\n{collected_specs[key]}\n"

    # Inject Architect artifacts for ConfigMap embedding.
    # The infra agent has no tool to read files — inject SQL and JSON content directly
    # so the LLM embeds the actual content instead of placeholders.
    if not is_databricks and any(k in selected_keys for k in ["generate_k8s_manifest"]):
        sql_path = "sql/setup_trino.sql"
        json_path = "dashboards/monitoring_specs.json"
        sql_content = read_file(sql_path) if os.path.exists(sql_path) else None
        json_content = read_file(json_path) if os.path.exists(json_path) else None
        if sql_content or json_content:
            system_prompt += "\n\n## ARCHITECT ARTIFACTS (embed verbatim in configmaps.yaml — no placeholders)\n"
            if sql_content:
                system_prompt += f"\n### sql/setup_trino.sql\n```sql\n{sql_content}\n```\n"
            if json_content:
                system_prompt += f"\n### dashboards/monitoring_specs.json\n```json\n{json_content}\n```\n"

    messages = [{"role": "system", "content": system_prompt}] + safe_recent_messages(state["messages"], limit=5)
    
    # 8. LLM INVOCATION
    response = llm_with_tools.invoke(messages)
    
    # 9. TOOL EXECUTION & STATE UPDATES
    new_messages = [response]
    updated_files = list(written_files)
    infra_success_detected = tf_done

    github_success = state.get("github_done", False)
    last_push_sha = state.get("last_push_sha", "")
    any_tool_error = False

    if response.tool_calls:
        for tool_call in response.tool_calls:
            t_name = tool_call["name"]
            # Sanitize args: LLMs occasionally emit "key=" instead of "key" in JSON
            t_args = {k.rstrip("="): v for k, v in tool_call["args"].items()}
            # project_id removed from terraform tool signatures — strip if LLM still passes it
            if t_name in ("write_terraform_config", "execute_terraform"):
                t_args.pop("project_id", None)
            try:
                # Skip files already written (unless in fix mode) to prevent re-generation loops.
                if t_name == "write_terraform_config" and not medic_triggered_fix:
                    raw = t_args.get("filename", "")
                    tracked = ("terraform/" + Path(raw).name).replace("\\", "/")
                    if tracked in updated_files:
                        result = f"Skipped: '{tracked}' already exists."
                        new_messages.append(ToolMessage(tool_call_id=tool_call["id"], content=result))
                        logger.info(f"⏭️ Skipping existing terraform file: {tracked}")
                        continue
                elif t_name == "push_to_github" and github_success:
                    result = "Skipped: push_to_github already succeeded in this turn."
                    new_messages.append(ToolMessage(tool_call_id=tool_call["id"], content=result))
                    logger.info("⏭️ Skipping duplicate push_to_github call.")
                    continue
                elif t_name in ("generate_dockerfile", "generate_k8s_manifest", "generate_github_action") and not medic_triggered_fix:
                    if t_name == "generate_dockerfile":
                        already_exists = any(f.lower().endswith("dockerfile") for f in updated_files)
                    elif t_name == "generate_k8s_manifest":
                        raw = os.path.basename(t_args.get("filename", ""))
                        clean = raw.replace(".yaml", "").replace(".yml", "")
                        tracked = f"k8s/{clean}.yaml"
                        already_exists = tracked.lower() in {f.lower() for f in updated_files}
                    else:  # generate_github_action
                        already_exists = any(".github/workflows" in f.lower() for f in updated_files)
                    if already_exists:
                        result = f"Skipped: file already exists."
                        new_messages.append(ToolMessage(tool_call_id=tool_call["id"], content=result))
                        logger.info(f"⏭️ Skipping existing file for tool: {t_name}")
                        continue

                result = full_tools_map[t_name].invoke(t_args)
                result_str = str(result)

                if "error" in result_str.lower() and t_name != "query_vector_store":
                    any_tool_error = True

                # A. Standard Capture (Smart Mapping)
                if t_name == "query_vector_store":
                    q = t_args.get("query", "").lower()
                    res_lower = result_str.lower()
                    matched = False
                    if any(x in q for x in ["terraform", "iac", "backend", "s3 bucket", "storage account", "gcs"]):
                        collected_specs["infra_standard_iac"] = result_str
                        matched = True
                    if any(x in q for x in ["kubernetes", "k8s", "manifest", "deployment", "orchestration"]):
                        collected_specs["infra_standard_k8s"] = result_str
                        matched = True
                    if any(x in q for x in ["github", "actions", "cicd", "workflow", "pipeline"]):
                        collected_specs["infra_standard_cicd"] = result_str
                        matched = True
                    if any(x in q for x in ["dockerfile", "docker", "non-root", "selective copy", "python image"]):
                        collected_specs["infra_standard_dockerfile"] = result_str
                        matched = True
                    if any(x in q for x in ["service account", "workload identity", "irsa", "iam.gke", "azure.workload", "serviceaccount"]):
                        collected_specs["infra_standard_service_account"] = result_str
                        matched = True

                    # Fallback: prevent infinite discovery loops when the LLM rephrases a query
                    # and none of the keyword conditions match. Store result for the first
                    # still-missing key so the agent always makes forward progress.
                    if not matched:
                        no_relevant = "no relevant guidelines found" in res_lower
                        still_missing = [k for k in required_standards if k not in collected_specs]
                        if still_missing and not no_relevant and result_str.strip():
                            target_key = still_missing[0]
                            collected_specs[target_key] = result_str
                            logger.info(f"🎯 Fallback Mapping: {target_key} secured from query result.")
                        else:
                            collected_specs[f"infra_spec_{q[:10]}"] = result_str

                # B. File Tracking — explicit from args to avoid brittle string parsing.
                # write_terraform_config returns "written to" (not "saved to"), so
                # string detection would silently miss all terraform files.
                if "error" not in result_str.lower():
                    if t_name == "write_terraform_config":
                        raw = t_args.get("filename", "")
                        tracked = ("terraform/" + Path(raw).name).replace("\\", "/")
                        if tracked not in updated_files:
                            updated_files.append(tracked)
                    elif t_name == "generate_dockerfile":
                        if "Dockerfile" not in updated_files:
                            updated_files.append("Dockerfile")
                    elif t_name == "generate_k8s_manifest":
                        raw = os.path.basename(t_args.get("filename", ""))
                        clean = raw.replace(".yaml", "").replace(".yml", "")
                        tracked = f"k8s/{clean}.yaml".replace("\\", "/")
                        if tracked not in updated_files:
                            updated_files.append(tracked)
                    elif t_name == "generate_github_action":
                        raw = t_args.get("workflow_name", "")
                        if not raw.endswith((".yaml", ".yml")):
                            raw += ".yaml"
                        tracked = f".github/workflows/{raw}".replace("\\", "/")
                        if tracked not in updated_files:
                            updated_files.append(tracked)

                # C. Execution Tracking & Action Locking (Searching for STATUS: SUCCESS)
                # This prevents the LLM from looping on the same command.
                if "STATUS: SUCCESS" in result_str.upper():
                    if t_name == "push_to_github":
                        github_success = True
                        logger.info("🔒 GitHub action locked successfully.")
                        sha_match = re.search(r"SHA:\s*([a-f0-9]{40})", result_str)
                        if sha_match:
                            last_push_sha = sha_match.group(1)
                            logger.info(f"📌 Commit SHA captured: {last_push_sha}")
                
                # D. Terraform Provisioning Check
                if t_name == "execute_terraform" and "apply complete" in result_str.lower():
                    infra_success_detected = True

            except Exception as e:
                logger.error(f"Tool {t_name} execution error: {e}")
                result = f"Error: {e}"
                any_tool_error = True

            new_messages.append(ToolMessage(tool_call_id=tool_call["id"], content=str(result)))

    # 10. RETURN UPDATED STATE
    return {
        "messages": new_messages,
        "written_files": updated_files,
        "collected_specs": collected_specs,
        "infra_provisioned": infra_success_detected,
        "github_done": github_success,
        "last_push_sha": last_push_sha,
        "infra_status": "completed" if github_success else "pending",
        "next_step": "supervisor",
        "last_agent": "infra",
        "medic_fix_requested": not github_success and state.get("medic_fix_requested", False),
        "agent_error": any_tool_error,
    }