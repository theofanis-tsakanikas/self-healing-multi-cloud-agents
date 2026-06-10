import os
import re
import logging
import json
from pathlib import Path
from langchain_core.messages import ToolMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from agents.llm_factory import get_llm
from agents.state import AgentState
from utils.prompt_utils import format_prompt
from utils.file_utils import read_file
from utils.message_utils import safe_recent_messages
from utils.config_utils import build_infra_context, build_databricks_infra_context
from utils.cloud_config import cloud_get_infra

# Import infrastructure automation tools
from agents.tools import (
    write_terraform_config, execute_terraform, generate_dockerfile,
    generate_k8s_manifest, generate_github_action, push_to_github, query_vector_store,
    validate_generated_code, patch_project_file, REPO_ROOT
)

from agents.constants import (
    DEFAULT_REQUIRED_DATABRICKS_TF_FILES,
    DEFAULT_REQUIRED_K8S_MANIFESTS,
    INFRA_PROMPT_FILE,
    K8S_PINNED_IMAGES,
    PROMPTS_DIR,
    TEMPERATURE,
)

logger = logging.getLogger("INFRA")

# The iac query is fully determined by the pipeline's cloud, so resolve it in Python and
# inject ONLY the matching one — the prompt no longer lists the other clouds' iac queries,
# so the LLM physically cannot fire a wrong-cloud iac query (it never sees those strings).
# (Same pattern as the ConfigMap embed / cloud-SDK import: resolve the deterministic bit
# here, don't leave it to LLM variance.) Strings must stay identical to the Smart-Mapping
# keywords below so the cloud-match guard still classifies them.
_IAC_QUERIES = {
    "aws": "Terraform Configuration. S3 backend for state storage. S3 bucket with versioning, encryption, lifecycle. IAM Access policy.",
    "azure": "Terraform Azure ADLS Gen2 storage account. AzureRM backend. Managed identity workload identity federation. Role assignment.",
    "gcp": "Terraform GCP Cloud Storage bucket. GCS backend. Service account workload identity binding. IAM member storage.objectAdmin.",
    "databricks": "Terraform Databricks pipeline. databricks_job spark_python_task existing_cluster_id, databricks_secret_scope secret db credentials, Unity Catalog catalog schema, Delta backend s3 state. No S3 bucket no IAM no Kubernetes.",
}


def files_exist_in_state(target_files: list, written_files: list) -> bool:
    """
    Checks if target_files are present in written_files.
    Uses lower-case comparison for cross-platform reliability.
    """
    if not target_files:
        return False
    written_files_lower = {f.lower() for f in written_files}
    return set(f.lower() for f in target_files).issubset(written_files_lower)


def _is_infra_allowed_file(filename: str) -> bool:
    """
    Security Filter — symmetric to the architect's _is_architect_allowed_file.
    Infra owns the deployment plane (Terraform, k8s manifests, Dockerfile, CI workflow)
    and must NEVER edit architect-owned data-plane artifacts: the pipeline script
    (scripts/*.py), the Trino DDL (sql/*.sql), the Grafana dashboard (dashboards/*.json)
    or requirements.txt. Without this guard a mis-routed fix lets Infra patch a .py and
    then cascade into regenerating unrelated artifacts (broken workflow pushed to repo).
    """
    norm = (filename or "").replace("\\", "/").lstrip("/")
    if not norm:
        return False
    name = Path(norm).name.lower()
    ext = Path(norm).suffix.lower()
    low = norm.lower()
    # Architect-owned → forbidden to Infra.
    if name == "requirements.txt":
        return False
    if ext in {".py", ".sql"}:
        return False
    if "dashboards/" in low:
        return False
    return True

def infra_node(state: AgentState, config: RunnableConfig = None):
    """
    Infrastructure agent node managing Terraform, Containerization, and CI/CD.

    Implements a 3-Phase Gate System:
    1. Discovery Phase: Only query_vector_store is available until standards are retrieved.
    2. Implementation Phase: Unlocks IaC, K8s, and Docker tools progressively.
    3. Action Lock: Permanently removes execution tools (Docker/Push) once SUCCESS is detected.

    config: LangGraph injects RunnableConfig (with LangSmith callbacks) automatically.
    Imperative auto-validation .invoke() calls intentionally do NOT pass it — they inherit the
    ambient trace context and nest under this node's run, instead of surfacing as separate ROOT
    traces in LangSmith.
    """
    logger.info("--- STARTING INFRASTRUCTURE NODE ---")
    llm = get_llm(temperature=TEMPERATURE)

    # 1. LOCAL STATE EXTRACTION
    written_files = state.get("written_files", [])
    tf_done = state.get("infra_provisioned", False)
    medic_triggered_fix = state.get("medic_fix_requested", False) or (state.get("last_agent") == "medic")
    project_id = state.get("project_id")
    collected_specs = dict(state.get("collected_specs", {}))

    # Resolve the container-registry URL — single source of truth is the BOOTSTRAP phase
    # (which creates the registry), resolved PER-CLOUD (see the branches below):
    #   • AWS   → SSM (bootstrap/aws/ssm.tf), read via cloud_get_infra
    #   • Azure → the ACR login server in azure_setup (no SSM)
    #   • GCP   → artifact_registry_url from bootstrap_outputs, else assembled from gcp_setup (no SSM)
    # cloud_get_infra falls back to .bootstrap_outputs.json for local dev. This resolution does
    # NOT rely on terraform output (the infra agent's terraform makes S3 + IAM, not the registry);
    # a legacy defensive capture from terraform output later in the tool loop is normally a no-op.
    # (`ecr_repository_url` is a legacy name; it holds ANY cloud's registry URL.)
    ecr_repository_url = state.get("ecr_repository_url", "")
    if not ecr_repository_url:
        _pipe_conf = state.get("raw_configs", {}).get("pipeline", {})
        _cloud = _pipe_conf.get("cloud_provider", "aws").lower()
        if _cloud == "azure":
            # Azure has no SSM. The image registry is the ACR login server created by
            # bootstrap and carried in the pipeline's azure_setup — read it directly.
            ecr_repository_url = (_pipe_conf.get("azure_setup", {}) or {}).get("acr_login_server", "")
        elif _cloud == "gcp":
            # GCP has no SSM either. Prefer the registry URL published by bootstrap
            # (.bootstrap_outputs.json, local dev); otherwise assemble it from the
            # Artifact Registry coords in gcp_setup + the PROJECT ID *value*.
            ecr_repository_url = cloud_get_infra("gcp", "artifact_registry_url") or ""
            if not ecr_repository_url:
                _gcp = _pipe_conf.get("gcp_setup", {}) or {}
                _region = _gcp.get("artifact_registry_region", "")
                _repo = _gcp.get("artifact_registry_repo", "")
                # project_id_env holds the NAME of the env var (e.g. "GCP_PROJECT_ID");
                # resolve it to its VALUE. Fall back to GCP_PROJECT_ID directly.
                _proj = os.getenv(_gcp.get("project_id_env", "GCP_PROJECT_ID")) or os.getenv("GCP_PROJECT_ID", "")
                if _region and _repo and _proj:
                    ecr_repository_url = f"{_region}-docker.pkg.dev/{_proj}/{_repo}"
            # GCP Artifact Registry images are HOST/PROJECT/REPOSITORY/IMAGE — both the
            # bootstrap output and the assembled URL stop at REPOSITORY, so append the
            # pipeline as the IMAGE name (mirrors Azure appending the image to the ACR host).
            # Without it `docker push` fails: "name invalid: Missing image name". Idempotent.
            if ecr_repository_url:
                _gcp_img = (project_id or "pipeline").replace("_", "-").lower()
                if not ecr_repository_url.rstrip("/").endswith("/" + _gcp_img):
                    ecr_repository_url = ecr_repository_url.rstrip("/") + "/" + _gcp_img
        else:
            # AWS: single source of truth is SSM (bootstrap/aws/ssm.tf), with a
            # .bootstrap_outputs.json fallback for local dev.
            ecr_repository_url = cloud_get_infra(_cloud, "ecr_repository_url") or ""
        if ecr_repository_url:
            logger.info(f"Image registry resolved for {_cloud}: {ecr_repository_url}")


    # 2. CONTEXT GENERATION
    raw_configs = state.get("raw_configs", {})
    pipeline_conf = raw_configs.get("pipeline", {})
    infra_conf = raw_configs.get("infrastructure", {})
    cloud_provider = pipeline_conf.get("cloud_provider", "aws").lower()

    provider = infra_conf.get("provider", "kubernetes").lower()
    is_databricks = provider == "databricks"

    try:
        if is_databricks:
            infra_context = build_databricks_infra_context(pipeline_conf, infra_conf)
            logger.info("🧱 Databricks provider detected. Using Databricks context.")
        else:
            infra_context = build_infra_context(pipeline_conf, infra_conf, raw_configs.get("database", {}))
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
        "query_vector_store": query_vector_store,
        "validate_generated_code": validate_generated_code,
        "patch_project_file": patch_project_file,
    }

    # 4. PHASE-GATE LOGIC (PROGRESSIVE TOOL LOCKING)
    # infra_standard_service_account is NOT a separate required key —
    # k8s_deployment_rules.md (Section 8) already contains the cloud-specific
    # ServiceAccount / IRSA / Workload Identity spec inside infra_standard_k8s.
    # Databricks needs ONLY IaC (databricks_job) + CI/CD (databricks-cli) standards —
    # no Kubernetes manifests, no Dockerfile. Forcing k8s/dockerfile discovery would make
    # the gate unsatisfiable with irrelevant standards. AWS/Azure/GCP keep the full set.
    if is_databricks:
        required_standards = ["infra_standard_iac", "infra_standard_cicd"]
    else:
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
                "CURRENT OPERATIONAL PHASE: DATABRICKS CI/CD. Generate the GitHub Actions "
                "workflow following the Databricks module in the CI/CD standard (§3.5) VERBATIM: "
                "actions/checkout + setup-terraform + databricks/setup-cli, upload the Spark "
                "script to DBFS (databricks fs cp), then `databricks jobs run-now` the job + poll "
                "the run to SUCCESS. The secret scope + databricks_job terraform is applied by the "
                "agent's execute_terraform (NOT this workflow); it reads the source DB connection "
                "from SSM (data aws_ssm_parameter), so there are NO TF_VAR_db_* and NO db_* "
                "terraform variables. Auth = DATABRICKS_HOST + DATABRICKS_TOKEN. "
                "NO docker build, NO kubectl, NO Dockerfile/K8s — Databricks manages its compute."
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
            tf_files_missing = not files_exist_in_state(tf_required, written_files)
            if tf_files_missing:
                # First run: files don't exist yet — need both write and execute
                selected_keys = ["write_terraform_config", "execute_terraform"]
            elif medic_triggered_fix:
                # Fix mode: files exist but execute failed. Allow targeted rewrite (dedup
                # in the execution loop gates which files can actually be overwritten based
                # on healing_context). Always include execute so the fix is applied immediately.
                selected_keys = ["write_terraform_config", "execute_terraform"]
            else:
                # Files exist, no fix needed — just re-run execute (e.g. state recovery)
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

            # Scenario B: medic_fix_requested + github_done=True.
            # Two sub-cases:
            #   B1 (healing_context empty): architect consumed it → fix already applied, just push.
            #   B2 (healing_context present): infra itself must fix first, THEN push.
            #      Use patch_project_file (surgical) for targeted edits; generate_k8s_manifest
            #      as fallback for structural rewrites. Never push before validation passes.
            if state.get("medic_fix_requested", False) and state.get("github_done", False):
                if state.get("healing_context", "").strip():
                    # patch_project_file only — generate_k8s_manifest is forbidden in fix mode.
                    # Full rewrites of multi-object files (prometheus: 4 objects, configmaps: 5)
                    # always risk losing objects not mentioned in the healing_context.
                    selected_keys = ["patch_project_file", "push_to_github"]
                else:
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

                if cloud_provider == "azure":
                    ecr_hint = (
                        f"\nAzure Container Registry login server (tag images as <login_server>/<image>:<sha>): {ecr_repository_url}"
                        if ecr_repository_url else
                        "\nAzure Container Registry: use CLOUD_SETUP.acr_login_server from your context as the image registry host."
                    )
                elif cloud_provider == "gcp":
                    ecr_hint = (
                        f"\nGCP Artifact Registry (tag images as <registry>/<image>:<sha>): {ecr_repository_url}"
                        if ecr_repository_url else
                        "\nGCP Artifact Registry: assemble {artifact_registry_region}-docker.pkg.dev/<GCP_PROJECT_ID>/{artifact_registry_repo} from CLOUD_SETUP."
                    )
                else:
                    ecr_hint = (
                        f"\nECR Repository URL (use this exact value, never write <AWS_ACCOUNT_ID>): {ecr_repository_url}"
                        if ecr_repository_url else
                        "\nECR Repository URL: not yet available — extract it from the execute_terraform output in conversation history."
                    )
                # Single source of truth for the pinned versions: agents/constants.py
                # K8S_PINNED_IMAGES (the same dict the validator enforces). Never re-literal here.
                _pinned_images = " | ".join(K8S_PINNED_IMAGES.values())
                orchestration_phase_instruction = (
                    f"CURRENT OPERATIONAL PHASE: IMPLEMENTATION — ORCHESTRATION. "
                    f"Generate ONLY these missing files: {missing_orchestration}. "
                    "Do NOT regenerate files that already exist."
                    f"{ecr_hint}"
                    "\n\nMANDATORY K8S POLICY (enforced by auto-validation):"
                    "\n• job.yaml: spec.backoffLimit=0 | envFrom secretRef '{project_id}-db-credentials' | env: PROJECT_ID, CLOUD_PROVIDER, TRINO_HOST, PUSHGATEWAY_URL"
                    "\n• configmaps.yaml: ALL 5 ConfigMaps in ONE file separated by ---: trino-sql-config (analytics), hive-catalog-config (analytics), grafana-dash-config (monitoring), grafana-datasource-config (monitoring), prometheus-config (monitoring, scrape: pushgateway.monitoring.svc.cluster.local:9091)"
                    f"\n• Pinned images: {_pinned_images}"
                ).format(project_id=project_id or "pipeline")

                # When healing_context is present the agent must patch existing files,
                # not regenerate them. Generate tools are added only for files that are
                # genuinely absent from written_files — never because medic_triggered_fix
                # is True, since that flag alone would cause existing files to be rewritten
                # (overwriting correct content) instead of surgically patched.
                _has_healing = bool(state.get("healing_context", "").strip())
                selected_keys = []
                if not docker_ready or (medic_triggered_fix and not _has_healing):
                    selected_keys.append("generate_dockerfile")
                if not k8s_ready or (medic_triggered_fix and not _has_healing):
                    selected_keys.append("generate_k8s_manifest")
                # generate_github_action unlocks only after K8s manifests and Dockerfile
                # are fully ready — the workflow references these artifacts.
                if (k8s_ready and docker_ready and not github_ready) or (medic_triggered_fix and not _has_healing):
                    selected_keys.append("generate_github_action")
                # NOTE: validate_generated_code is NOT added to selected_keys —
                # it runs automatically in Python after every generate_* call (see
                # auto-validation block in the tool execution loop below).
            else:
                missing_orchestration = []
                orchestration_phase_instruction = None
                # Provide execution tools ONLY if they haven't succeeded yet (Action Lock)
                # execute_docker_command is intentionally excluded — build/push is handled
                # by the GitHub Actions workflow after push_to_github.
                if not state.get("github_done", False) or medic_triggered_fix:
                    selected_keys.append("push_to_github")

    # --- GATE 3: MEDIC OVERRIDE ---
    # healing_context (injected into system prompt) already contains the Medic's
    # KB-researched fix — no need to re-query. query_vector_store is NOT re-enabled
    # here to prevent the LLM from running a full re-discovery cycle instead of
    # applying the ready-made fix.
    # push_to_github MUST be available: the agent rewrites the file and must push
    # in the same turn — without it the fix is never deployed.
    if medic_triggered_fix:
        has_healing = bool(state.get("healing_context", "").strip())
        # Skip query_vector_store when fix came through architect: architect already
        # applied the patch and cleared healing_context — no KB re-lookup needed.
        coming_from_architect = state.get("last_agent") == "architect"
        if not has_healing and not coming_from_architect and "query_vector_store" not in selected_keys:
            # Fallback: no healing_context (edge case) — allow KB lookup as before
            selected_keys.append("query_vector_store")
            logger.info("🔧 MEDIC BYPASS: No healing_context — re-enabling query_vector_store as fallback.")
        if "patch_project_file" not in selected_keys:
            selected_keys.append("patch_project_file")
        if "push_to_github" not in selected_keys:
            selected_keys.append("push_to_github")
        logger.info("🔧 MEDIC BYPASS: patch_project_file + push_to_github enabled for fix cycle.")

    # 5. EARLY EXIT GATE
    # If all files exist, all actions are done, and standards are met, signal completion.
    if not selected_keys and has_all_standards:
        logger.info("🎯 MISSION ACCOMPLISHED: Infrastructure node finalized.")
        return {
            "messages": [HumanMessage(content="INFRA_COMPLETE: Infrastructure and CI/CD are finalized.")],
            "infra_status": "completed",
            "next_step": "supervisor",
            "last_agent": "infra",
            "healing_context": "",  # Clear — no fix in progress
        }

    # 6. LLM BINDING (Injecting only allowed tools for the current phase)
    # Note: parallel tool calls are NOT explicitly disabled here. If OpenAI ever wraps calls in
    # multi_tool_use.parallel and LangChain misparses them (corrupting tool args), pass
    # parallel_tool_calls=False to the bind_tools calls below.
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

    # Inject Medic's healing instructions before the phase text.
    # Placed in the system prompt (not message history) for maximum LLM attention.
    # Cleared by this node's return dict so it doesn't leak into future turns.
    healing_context = state.get("healing_context", "")
    if healing_context and medic_triggered_fix:
        system_prompt += (
            f"\n\n🚨 MANDATORY FIX — TOP PRIORITY (from Medic diagnostic):\n"
            f"{healing_context}\n"
            f"Apply this fix exactly as described. "
            f"Only call query_vector_store if the instructions above explicitly require it."
        )

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
            "The exact fix instructions are in the MANDATORY FIX section of your system prompt above — read them first. "
            "Step 1: Apply the fix by rewriting ONLY the affected file using the appropriate generation tool "
            "(generate_github_action for CI/CD errors, generate_k8s_manifest for Kubernetes errors, etc.). "
            "Step 2: Immediately call push_to_github to deploy the fix. "
            "CRITICAL RULES: "
            "(1) query_vector_store is only needed if the MANDATORY FIX section above says so. "
            "(2) You MUST call push_to_github in this same turn after rewriting the file. "
            "(3) An empty Knowledge Base result is never a reason to take no action."
        )
    else:
        phase_text = orchestration_phase_instruction or "CURRENT OPERATIONAL PHASE: IMPLEMENTATION"

    system_prompt += f"\n\n{phase_text}"

    # Pre-resolved iac query (query 1) — inject ONLY this pipeline's cloud variant so the LLM
    # cannot fire a wrong-cloud iac query (it never sees the other strings). Only while the
    # iac standard is still missing, so a populated key is never re-queried.
    if not has_all_standards and "infra_standard_iac" not in collected_specs:
        _iac_key = "databricks" if is_databricks else cloud_provider
        _iac_query = _IAC_QUERIES.get(_iac_key, _IAC_QUERIES["aws"])
        system_prompt += (
            "\n\n## 🔴 IAC QUERY (this IS query 1 — issue it VERBATIM; never any other "
            f'cloud\'s iac query):\n  query_vector_store(query="{_iac_query}")\n'
            f"Pre-resolved for this pipeline's cloud ({_iac_key}); it is the ONLY iac query that exists here.\n"
        )
        if is_databricks:
            system_prompt += (
                "🧱 DATABRICKS: issue EXACTLY TWO queries — this iac query + the CI/CD query. "
                "Do NOT issue the K8s or Dockerfile queries (Databricks has neither).\n"
            )

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

        # ALL infra standards come from Pinecone via query_vector_store — same mechanism as
        # the Architect, and chunking-ready (a future split into chunks keeps working; a
        # fetch-by-id or disk read would not). The Smart Mapping above now assigns each query
        # result to exactly one standard key (mutually-exclusive, distinctive keywords), so the
        # old disk-override that masked the key-swapping is gone. Requires the knowledge base to
        # be synced to Pinecone (run_agent.yml sync_knowledge_base: sync after editing standards).
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
            # The Trino DDL + Grafana dashboard JSON are injected VERBATIM from disk by
            # generate_k8s_manifest. The LLM must NOT re-type them: re-typing a ~150-line JSON
            # blows the output budget and TRUNCATES the later ConfigMaps (and can corrupt the
            # JSON). It outputs a short placeholder token instead; the tool fills in the real file.
            system_prompt += (
                "\n\n## CONFIGMAP EMBEDS — OUTPUT THE PLACEHOLDER TOKEN, NOT THE CONTENT\n"
                "In configmaps.yaml, for these two block-scalar values output ONLY the exact "
                "one-line token shown — do NOT paste the file content (the deploy tool injects "
                "the real, validated file verbatim, so you never re-type/truncate/corrupt them):\n"
                "  trino-sql-config → data.setup_trino.sql: |\n    __EMBED_SETUP_TRINO_SQL__\n"
                "  grafana-dash-config → data.monitoring_specs.json: |\n    __EMBED_MONITORING_SPECS_JSON__\n"
                "Write the OTHER ConfigMap values (hive.properties, prometheus.yml, "
                "datasource.yaml, dashboard-provider.yaml) IN FULL as usual. Keeping the two big "
                "blobs as tokens keeps the file short so all FIVE ConfigMaps fit without truncation.\n"
            )

    messages = [{"role": "system", "content": system_prompt}] + safe_recent_messages(state["messages"], limit=5)

    # 8. LLM INVOCATION
    response = llm_with_tools.invoke(messages)

    # 9. TOOL EXECUTION & STATE UPDATES
    new_messages = [response]
    updated_files = list(written_files)
    infra_success_detected = tf_done
    validation_errors: list[str] = []  # Accumulated for error_log → Medic signal

    github_success = state.get("github_done", False)
    push_attempted = False  # True after first push_to_github call — blocks same-turn retry
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

            # Ownership guard: Infra must never edit architect-owned data-plane files
            # (scripts/*.py, sql/*.sql, dashboards/*.json, requirements.txt). Symmetric to
            # the architect's _is_architect_allowed_file. Blocks patch_project_file (the only
            # tool that takes an arbitrary path) from straying into another agent's artifacts
            # — the root of the cascade where a mis-routed .py fix dragged Infra off course.
            if t_name in ("patch_project_file", "write_terraform_config", "write_project_file"):
                _target = t_args.get("filename", "")
                if _target and not _is_infra_allowed_file(_target):
                    result = (
                        f"Policy Error: Infra is not permitted to modify '{_target}' — it is an "
                        f"architect-owned artifact (pipeline script / Trino SQL / dashboard / "
                        f"requirements). This fix belongs to the architect, not infra."
                    )
                    any_tool_error = True
                    new_messages.append(ToolMessage(tool_call_id=tool_call["id"], content=result))
                    logger.warning(f"🚫 Infra ownership guard: blocked edit to '{_target}'.")
                    continue

            try:
                # Skip files already written to prevent re-generation loops.
                # In fix mode: allow overwrite ONLY for the file explicitly targeted by
                # healing_context (e.g. "fix providers.tf" → only providers.tf is rewritten).
                # All other existing TF files are still skipped — no unnecessary rewrites.
                if t_name == "write_terraform_config":
                    raw = t_args.get("filename", "")
                    tracked = ("terraform/" + Path(raw).name).replace("\\", "/")
                    filename_base = Path(raw).name.lower()
                    if tracked in updated_files:
                        # In fix mode, allow overwrite only if this file is the fix target
                        is_fix_target = (
                            medic_triggered_fix
                            and filename_base in healing_context.lower()
                        )
                        if not is_fix_target:
                            result = f"Skipped: '{tracked}' already exists and is not the fix target."
                            new_messages.append(ToolMessage(tool_call_id=tool_call["id"], content=result))
                            logger.info(f"⏭️ Skipping existing terraform file: {tracked}")
                            continue
                elif t_name == "push_to_github" and (github_success or push_attempted):
                    reason = "already succeeded" if github_success else "already attempted (failed) — Medic will re-route"
                    result = f"Skipped: push_to_github {reason} in this turn. Do not retry push in the same turn."
                    new_messages.append(ToolMessage(tool_call_id=tool_call["id"], content=result))
                    logger.info(f"⏭️ Skipping duplicate push_to_github call ({reason}).")
                    continue
                elif t_name == "generate_k8s_manifest":
                    raw = os.path.basename(t_args.get("filename", ""))
                    clean = raw.replace(".yaml", "").replace(".yml", "")
                    tracked = f"k8s/{clean}.yaml"
                    already_exists = tracked.lower() in {f.lower() for f in updated_files}
                    if already_exists:
                        if medic_triggered_fix:
                            # Fix mode: only regenerate the file explicitly named in healing_context.
                            # Prevents full K8s stack rewrites when only one manifest is broken.
                            filename_base = Path(raw).name.lower()
                            is_fix_target = filename_base in healing_context.lower()
                            if not is_fix_target:
                                result = (
                                    f"Skipped: '{tracked}' is not the fix target. "
                                    "Only files named in healing_context may be regenerated in fix mode."
                                )
                                new_messages.append(ToolMessage(tool_call_id=tool_call["id"], content=result))
                                logger.info(f"⏭️ Fix mode: skipping '{tracked}' — not in healing_context.")
                                continue
                        else:
                            result = f"Skipped: '{tracked}' already exists."
                            new_messages.append(ToolMessage(tool_call_id=tool_call["id"], content=result))
                            logger.info(f"⏭️ Skipping existing K8s manifest: {tracked}")
                            continue

                elif t_name in ("generate_dockerfile", "generate_github_action") and not medic_triggered_fix:
                    if t_name == "generate_dockerfile":
                        already_exists = any(f.lower().endswith("dockerfile") for f in updated_files)
                    else:  # generate_github_action
                        already_exists = any(".github/workflows" in f.lower() for f in updated_files)
                    if already_exists:
                        result = "Skipped: file already exists."
                        new_messages.append(ToolMessage(tool_call_id=tool_call["id"], content=result))
                        logger.info(f"⏭️ Skipping existing file for tool: {t_name}")
                        continue

                elif t_name == "push_to_github" and medic_triggered_fix and validation_errors:
                    # Validate-before-push gate: block push if any file still fails validation.
                    # This prevents pushing broken artifacts and losing the healing_context to a
                    # secondary push-failure error that overwrites the original diagnosis.
                    result = (
                        "BLOCKED: Cannot push — the following files still fail validation. "
                        "Fix them before calling push_to_github:\n" + "\n".join(validation_errors)
                    )
                    any_tool_error = True
                    new_messages.append(ToolMessage(tool_call_id=tool_call["id"], content=result))
                    logger.warning("🚫 PUSH BLOCKED: outstanding validation errors in fix mode.")
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
                        # The prompt lists ALL THREE clouds' iac queries; when discovery spans
                        # turns the agent sometimes fires another cloud's iac query too. Both map
                        # here, so an unguarded overwrite of the shared infra_standard_iac key
                        # replaces the correct standard (e.g. an Azure azurerm-backend result
                        # lands in a GCP pipeline → terraform init hits the non-existent Azure RG).
                        # Store the result ONLY if the query targets THIS pipeline's cloud.
                        if "azure" in q or "adls" in q or "azurerm" in q:
                            _q_cloud = "azure"
                        elif "gcs" in q or "gcp" in q or "google cloud storage" in q:
                            _q_cloud = "gcp"
                        elif "s3" in q or "dynamodb" in q:
                            _q_cloud = "aws"
                        else:
                            _q_cloud = cloud_provider  # generic terraform query → current cloud
                        if is_databricks or _q_cloud == cloud_provider:
                            collected_specs["infra_standard_iac"] = result_str
                            matched = True
                        else:
                            matched = True  # consume it (don't fall through to fallback) but DROP it
                            logger.warning(
                                f"🚫 Ignored wrong-cloud iac query ({_q_cloud}) for a "
                                f"{cloud_provider} pipeline — kept the correct infra_standard_iac."
                            )
                    # Mutually-exclusive elif (NOT separate ifs) with DISTINCTIVE keywords:
                    # each query result maps to exactly ONE standard. Previously overlapping
                    # keywords across separate ifs ("deployment" in cicd's query also matched
                    # the k8s set; "pipeline" in dockerfile's query matched cicd) stored one
                    # result under TWO keys and the last write won — the cicd result overwrote
                    # infra_standard_k8s. That key-swapping is exactly what the disk-override
                    # masked; first-match-wins on distinctive terms removes the need for it.
                    elif "dockerfile" in q:
                        collected_specs["infra_standard_dockerfile"] = result_str
                        matched = True
                    elif any(x in q for x in ["kubernetes", "k8s", "job.yaml", "initcontainer", "manifest", "serviceaccount", "volumemount"]):
                        collected_specs["infra_standard_k8s"] = result_str
                        matched = True
                    elif any(x in q for x in ["github", "actions", "cicd", "workflow"]):
                        collected_specs["infra_standard_cicd"] = result_str
                        matched = True
                    elif any(x in q for x in ["workload identity", "irsa", "iam.gke", "azure.workload"]):
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

                # B. File Tracking + AUTO-VALIDATE for generated artifacts.
                # write_terraform_config returns "written to" (not "saved to"), so
                # string detection would silently miss all terraform files.
                # Auto-validation runs in Python — does not depend on the LLM calling
                # validate_generated_code. Result is appended to result_str so the LLM
                # sees any errors in the same ToolMessage and fixes them immediately.
                if "error" not in result_str.lower():
                    auto_validate_path = None

                    if t_name == "write_terraform_config":
                        raw = t_args.get("filename", "")
                        tracked = ("terraform/" + Path(raw).name).replace("\\", "/")
                        if tracked not in updated_files:
                            updated_files.append(tracked)

                    elif t_name == "generate_dockerfile":
                        if "Dockerfile" not in updated_files:
                            updated_files.append("Dockerfile")
                        auto_validate_path = "Dockerfile"

                    elif t_name == "generate_k8s_manifest":
                        raw = os.path.basename(t_args.get("filename", ""))
                        clean = raw.replace(".yaml", "").replace(".yml", "")
                        tracked = f"k8s/{clean}.yaml".replace("\\", "/")
                        if tracked not in updated_files:
                            updated_files.append(tracked)
                        auto_validate_path = tracked

                    elif t_name == "generate_github_action":
                        # The tool derives the filename from project_id internally —
                        # t_args never contains "workflow_name". Mirror the tool's logic.
                        proj = t_args.get("project_id", "")
                        raw = f"{proj}_pipeline.yml" if proj else ""
                        if raw:
                            tracked = f".github/workflows/{raw}".replace("\\", "/")
                            if tracked not in updated_files:
                                updated_files.append(tracked)
                            gha_path = os.path.join(str(REPO_ROOT), ".github", "workflows", raw)
                            auto_validate_path = gha_path

                    elif t_name == "patch_project_file":
                        # Auto-validate the patched file immediately — same guarantee as
                        # generate_* tools. The patch may have introduced a regression or
                        # failed to fix the target issue; catching it here prevents a
                        # silent bad state from reaching push_to_github.
                        patch_filename = t_args.get("filename", "")
                        if patch_filename:
                            auto_validate_path = patch_filename

                    # Trigger auto-validation for files that have a validator.
                    # Do NOT pass `config`: let it inherit the ambient trace context so it nests
                    # under this node's run instead of surfacing as a separate ROOT trace.
                    if auto_validate_path and os.path.exists(auto_validate_path):
                        from agents.tools import validate_generated_code as _validate
                        validation_result = str(
                            _validate.invoke({"filename": auto_validate_path})
                        )
                        if "VALIDATION FAILED" in validation_result:
                            any_tool_error = True
                            logger.warning(f"⚠️ AUTO-VALIDATION FAILED: {auto_validate_path}")
                            validation_errors.append(f"{auto_validate_path}: {validation_result}")
                            # Remove from tracking so the LLM can regenerate on the next attempt.
                            # Without this, the file stays in written_files and docker_ready/k8s_ready
                            # becomes True, causing the broken file to be pushed as-is.
                            tracked_key = None
                            if t_name == "generate_dockerfile":
                                tracked_key = "Dockerfile"
                            elif t_name == "generate_k8s_manifest":
                                tracked_key = tracked  # set above when file was added
                            elif t_name == "generate_github_action":
                                tracked_key = tracked  # set above when file was added
                            if tracked_key and tracked_key in updated_files:
                                updated_files.remove(tracked_key)
                                logger.info(f"↩️ Removed '{tracked_key}' from tracking — validation failed, will regenerate.")
                            result_str = (
                                f"{result_str}\n\n"
                                f"AUTO-VALIDATION FAILED — fix these errors and call the same generation tool again with corrected content:\n"
                                f"{validation_result}"
                            )
                        else:
                            logger.info(f"✅ AUTO-VALIDATION PASSED: {auto_validate_path}")
                            result_str = f"{result_str}\nAUTO-VALIDATION: CLEAN ✓"

                # C. Execution Tracking & Action Locking (Searching for STATUS: SUCCESS)
                # This prevents the LLM from looping on the same command.
                if t_name == "push_to_github":
                    push_attempted = True
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
                    # LEGACY/DEFENSIVE capture: if an ECR URL ever appears in the terraform
                    # output, use it. Normally a NO-OP — the infra agent's terraform makes
                    # S3 + IAM (not the registry), and the authoritative ecr_repository_url is
                    # already resolved at the top of this node from the bootstrap (SSM/ACR/AR).
                    # AWS-only pattern; kept as a last-resort override (safe to remove during
                    # the next infra re-validation).
                    ecr_match = re.search(
                        r'ecr_repository_url\s*=\s*"([^"]+)"', result_str
                    ) or re.search(
                        r'(\d{12}\.dkr\.ecr\.[a-z0-9-]+\.amazonaws\.com/[^\s"\'>\n]+)',
                        result_str,
                    )
                    if ecr_match:
                        ecr_repository_url = ecr_match.group(1).rstrip("/")
                        logger.info(f"📦 ECR repo URL captured: {ecr_repository_url}")
                    else:
                        logger.debug("ECR URL not in terraform output (expected) — using the bootstrap/SSM value resolved at the top of this node.")

            except Exception as e:
                logger.error(f"Tool {t_name} execution error: {e}")
                result = f"Error: {e}"
                any_tool_error = True

            new_messages.append(ToolMessage(tool_call_id=tool_call["id"], content=str(result)))

    # 10. RETURN UPDATED STATE
    # Write validation errors to error_log so Medic reads them as a structured signal
    # instead of having to infer the problem from message history.
    error_log = ("\n\n".join(validation_errors)) if validation_errors else state.get("error_log", "")

    return {
        "messages": new_messages,
        "written_files": updated_files,
        "collected_specs": collected_specs,
        "infra_provisioned": infra_success_detected,
        "ecr_repository_url": ecr_repository_url,
        "github_done": github_success,
        "last_push_sha": last_push_sha,
        "infra_status": "completed" if github_success else "pending",
        "next_step": "supervisor",
        "last_agent": "infra",
        "medic_fix_requested": not github_success and state.get("medic_fix_requested", False),
        "agent_error": any_tool_error,
        "healing_context": "",
        "error_log": error_log,
    }
