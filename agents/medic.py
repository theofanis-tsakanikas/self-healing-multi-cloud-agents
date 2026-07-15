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
    REPO_ROOT,
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


# Standards that are CODE-OWNED since the codegen migration: architect/infra no longer
# retrieve them during discovery, so collected_specs never carries them. When a failure
# matches one of these categories the medic fetches the standard from Pinecone ITSELF —
# deterministically, at the Python layer (never relying on the LLM to think of querying).
# Query strings = the canonical discovery queries the agents used to issue.
_CODE_OWNED_STANDARD_QUERIES = {
    "arch_standard_grafana": (
        "grafana dashboard json specifications. Panels, fields, templating variables, "
        "stable uid, $project_id $cloud_provider"
    ),
    "infra_standard_k8s": (
        "Kubernetes job.yaml initContainers serviceAccountName volumeMounts volumes "
        "hive-catalog-config grafana-dash-config prometheus-config DESTINATION_URI "
        "namespace analytics monitoring. Deployment Trino Grafana Prometheus Pushgateway "
        "AWS Glue metastore hive connector Section 8.4"
    ),
    "infra_standard_cicd": (
        "Github actions cicd pipelines. Workflow trigger and structure, deployment "
        "execution, checkout, github secrets"
    ),
    "infra_standard_dockerfile": (
        "Dockerfile python pipeline image non-root user selective COPY CMD script path"
    ),
}


def _resolve_relevant_standards(error_context: str, indicators: dict,
                                collected_specs: dict, fetch) -> dict:
    """Map the current failure to the standards the medic should see.

    Pre-loaded standards (LLM-owned artifacts) come from collected_specs; standards for
    CODE-OWNED artifacts are fetched from the KB on demand via `fetch` (they are no
    longer retrieved at generation time, but they remain the medic's diagnostic
    reference — the spec of what a correct artifact looks like)."""
    matched = {}
    for key, inds in indicators.items():
        if not any(ind in error_context for ind in inds):
            continue
        if key in collected_specs:
            matched[key] = collected_specs[key]
        elif key in _CODE_OWNED_STANDARD_QUERIES:
            try:
                result = str(fetch(_CODE_OWNED_STANDARD_QUERIES[key]))
            except Exception:
                continue
            if result.strip() and "no relevant guidelines" not in result.lower():
                matched[key] = result
    return matched


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


_AUTOVAL_FAIL_RE = re.compile(r"AUTO-VALIDATION FAILED — fix these errors in '([^']+)'")


def _latest_autovalidation_failure(messages: list) -> str:
    """Return the file most recently flagged FAILED by the architect's auto-validation.

    The architect runs validate_generated_code in Python (NOT as an LLM tool call) and
    appends "AUTO-VALIDATION FAILED — fix these errors in '<file>'" to the
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


_CI_FILE_RE = re.compile(r'(?:/app/)?(scripts/[\w./\-]+\.py|sql/[\w./\-]+\.sql)')


def _extract_ci_failed_file(messages: list) -> str:
    """For a CI-LOG runtime failure (no validate_generated_code result), find the failing PROJECT
    artifact in the CI traceback — e.g. `File "/app/scripts/pipe_x.py"` → `scripts/pipe_x.py` — so
    the architect can target it. Without a filename in healing_context the architect rejects its
    own patch ("not the fix target") and the fix loops. Scans the most recent messages (the fetch
    output). Library frames (`/usr/local/lib/.../pandas/...`) don't match. Returns "" if none."""
    for msg in reversed(messages[-12:]):
        content = getattr(msg, "content", "") or ""
        if not isinstance(content, str):
            content = str(content)
        hits = _CI_FILE_RE.findall(content)
        if hits:
            return hits[-1]   # the deepest/last project frame in the traceback
    return ""


def _terraform_command_failure(messages: list) -> str:
    """Return the failure text of the most recent `terraform` command that FAILED, else "".

    execute_terraform (agents/tools.py) returns "SUCCESS: Terraform <cmd>\\n…" on success and
    "FAILED: Terraform <cmd>\\nERROR: …" on failure. A terraform apply/plan that fails is an
    UNAMBIGUOUS infra bug — the fix is terraform/main.tf, never a script — so the Medic must route it
    to infra DETERMINISTICALLY, independent of whether the LLM quoted the error well enough to pass
    the request_fix provenance gate (a paraphrased terraform error is not a verbatim substring of the
    tool output → the gate drops it → the infra failure otherwise falls through to the architect
    default and architect↔medic loops to the recursion limit). Only the NEWEST terraform result
    counts: a later SUCCESS (the re-apply after a heal) supersedes an earlier FAILED, so a healed run
    does not re-trigger. Operational lock errors surface as PENDING/STATE_LOCK (not FAILED) and are
    excluded — no code change fixes them."""
    for msg in reversed(messages[-20:]):
        content = getattr(msg, "content", "") or ""
        if not isinstance(content, str):
            content = str(content)
        stripped = content.lstrip()
        if "SUCCESS: Terraform" in content:
            return ""  # newest terraform result is a success → nothing to fix
        if stripped.startswith("FAILED: Terraform"):
            if "STATE_LOCK_ERROR" in content or stripped.startswith("PENDING:"):
                return ""  # operational, not a code bug
            return content[:1500]
    return ""


# CI-runtime error signatures whose owner is UNAMBIGUOUS = infra. A CI-LOG failure (no validation
# result) is normally routed to the architect via the failing scripts/*.py frame in the traceback —
# but a missing PROVISIONED-RESOURCE failure surfaces AT the script's line yet the fix is the
# Terraform, NOT the script:
#   • a JVM class / Maven library the job needs is not attached  → fix the databricks_job `library`;
#   • a Databricks secret the script reads (dbutils.secrets.get) does not exist → fix the
#     `databricks_secret` key in the Terraform so it matches what the script requests.
# These signatures never appear in the object-storage clouds' pandas tracebacks (they are
# JVM/Databricks only), so matching them is additive — it cannot mis-route an AWS/GCP/Azure script
# error. Anything NOT matched here keeps the existing script-file routing / the LLM's target_agent.
_CI_INFRA_SIGNATURES = (
    "classnotfoundexception",      # e.g. org.postgresql.Driver — JDBC driver library missing
    "noclassdeffounderror",
    "library installation failed",
    "failed to install library",
    "secret does not exist",       # dbutils.secrets.get key ≠ databricks_secret key → fix Terraform
    "resource_does_not_exist",     # Databricks API code accompanying a missing secret/scope
    # Object-storage cloud IAM / provisioning runtime failures (AWS/GCP/Azure). They surface at the
    # pipeline's cloud write/connect line (a script frame), but the fix is the generated Terraform IAM
    # policy / bucket — NOT the script. Without these the router falls back to the script frame and
    # mis-sends an IAM/provisioning problem to the architect (who cannot grant a permission). These
    # tokens never appear in a pandas/Spark logic traceback (KeyError/ValueError/…), so they are safe.
    "accessdenied", "access denied",          # AWS S3/Glue/SSM
    "not authorized to perform",              # AWS IAM
    "invalidaccesskeyid", "signaturedoesnotmatch",  # AWS credential
    "nosuchbucket", "no such bucket",         # bucket not provisioned
    "does not have storage.",                 # GCP storage.objects.* IAM denial
    "invalidresourcename",                    # Azure Blob (e.g. wrong container/netloc)
    "authorizationpermissionmismatch",        # Azure RBAC on the storage account
    # Credential-resolution failure: cloud_get() returned None (AWS IRSA missing ssm:GetParameter, or an
    # unset GCP/Azure env var) → db_host is the string "None" → the DB connect fails. The plumbing is the
    # generated IAM policy / K8s secret, NOT the script (which reads the host via cloud_get, never
    # hardcodes it) — so this is infra, per CLAUDE.md ("Missing it → cloud_get() returns None → host name
    # 'None' error"). Without it the create_engine frame mis-routes the credential plumbing to architect.
    'host name "none"',
)

# A CI-runtime error carrying one of these is a SCRIPT-LOGIC bug → architect (on ANY cloud). These
# are Python/Spark logic errors the architect owns; they are NOT provisioning/infra. Listing them
# lets the medic route a script bug to the architect DETERMINISTICALLY instead of relying on the
# LLM's target_agent (which has mis-routed before).
_CI_SCRIPT_SIGNATURES = (
    "keyerror", "valueerror", "typeerror", "nameerror", "attributeerror", "indexerror",
    "unresolved_column", "analysisexception", "cannot resolve",
)

# Markers that the failing run is a DATABRICKS run (they NEVER appear in an object-storage
# pandas/Trino traceback). Used as a generalised fallback: an UNRECOGNISED Databricks runtime error
# (not a known infra signature, not a script-logic bug) is far more likely an infra/provisioning
# issue than a Spark logic bug — the Spark script is validated; the failures are
# permission/config/resource. So default it to infra rather than the script-frame architect. Object-
# storage errors lack these markers → they fall through to '' (the LLM/script-frame default).
_CI_DATABRICKS_CONTEXT = ("py4j", "dbutils", "databricks", "unity catalog")


def _ci_error_owner(messages: list) -> str:
    """3-way owner for a CI-LOG runtime failure (no validate_generated_code result to map):
      - 'architect' → a SCRIPT-LOGIC bug (KeyError/ValueError/AnalysisException/…) on any cloud;
      - 'infra'     → a known infra signature (missing library/secret/resource) OR any OTHER
                      Databricks runtime error (those are predominantly provisioning/permission,
                      not Spark logic — generalises beyond the fixed signature list);
      - ''          → undetermined (object-storage non-logic error) → let the LLM target_agent /
                      script-frame default decide.
    Scans the most recent messages (the fetch_github_action_logs output)."""
    blob = " ".join(
        (getattr(m, "content", "") if isinstance(getattr(m, "content", ""), str)
         else str(getattr(m, "content", "")))
        for m in messages[-12:]
    ).lower()
    if any(sig in blob for sig in _CI_SCRIPT_SIGNATURES):
        return "architect"
    if any(sig in blob for sig in _CI_INFRA_SIGNATURES):
        return "infra"
    if any(sig in blob for sig in _CI_DATABRICKS_CONTEXT):
        return "infra"
    return ""


def _normalize_error_sig(text: str) -> str:
    """Strip VOLATILE tokens from an error string so the fix-loop convergence guard treats the SAME
    underlying failure as identical across rounds even when line numbers / run-ids / timestamps /
    hex addresses / ephemeral tmp paths differ. Without this, an error whose text shifts every
    attempt (e.g. a new `/tmp/tmpXXXX.py` path or a different traceback line number) produces a
    fresh signature each round → fix_attempt never reaches the escalation threshold → the heal
    loops to the graph recursion limit instead of surfacing to the user."""
    t = (text or "").lower()
    t = re.sub(r"/tmp/\S+", "/tmp/X", t)                    # ephemeral spark tmp script paths
    t = re.sub(r"\b[0-9a-f]{8,}\b", "X", t)                 # hex ids / shas / dashboard+run ids
    t = re.sub(r"\d{4}-\d{2}-\d{2}[t ][\d:.,+-]+", "T", t)  # iso timestamps
    t = re.sub(r"\d+", "N", t)                              # line numbers, counts, numeric run-ids
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _databricks_secret_key_exact_fix(messages: list) -> tuple[str, str] | None:
    """For a Databricks 'Secret does not exist … key: <wanted>' failure, compute the EXACT one-line
    patch for terraform/main.tf so the infra agent does not have to GUESS which line to change.

    The agent reliably mis-targets this: the error names the SCOPE prominently ('scope: X and key:
    db_password'), so the LLM patches the `scope`/`name` instead of the `databricks_secret` `key`
    value — leaving the real bug (`key = "postgres_password"`) untouched and the heal looping.

    Reads the CURRENT `databricks_secret` `key = "<current>"` line VERBATIM (whitespace and all) and
    returns (old_line, new_line) with only the value swapped to <wanted>. Returns None when not
    applicable (no secret error, file unreadable, key already correct, or no key line found)."""
    blob = "\n".join(str(getattr(m, "content", "")) for m in messages[-12:])
    m = re.search(r"[Ss]ecret does not exist.*?key:\s*([A-Za-z0-9_./-]+)", blob)
    if not m:
        return None
    wanted = m.group(1).strip()
    try:
        tf = read_file(str(REPO_ROOT / "terraform" / "main.tf"))
    except Exception:
        return None
    in_secret = False
    for line in tf.splitlines():
        # `resource "databricks_secret" "..."` — NOT `databricks_secret_scope` (the closing quote
        # after `databricks_secret` excludes the `_scope` variant).
        if re.search(r'resource\s+"databricks_secret"', line):
            in_secret = True
            continue
        if in_secret:
            km = re.match(r'(\s*key\s*=\s*")([^"]+)(".*)$', line)
            if km:
                if km.group(2) == wanted:
                    return None  # already correct
                return (line, km.group(1) + wanted + km.group(3))
            if re.match(r'\s*(resource|data)\s+"', line):  # left the secret block without a key
                in_secret = False
    return None


def _accumulate_healing_context(existing: str, new_chunk: str) -> str:
    """Append a new fix chunk to the running healing_context WITHOUT overwriting.
    Multiple request_fix calls in one medic turn must ALL reach the target agent, so
    chunks are joined with a separator (blank entries dropped), never replaced.
    Extracted as a pure function so the accumulate-not-overwrite invariant is testable."""
    return "\n\n---\n\n".join(filter(None, [existing, new_chunk]))


def _evidence_has_provenance(quote: str, messages: list) -> bool:
    """True if the (whitespace-normalized) evidence_quote actually appears in a real message/tool
    output. Guards against an LLM FABRICATING an evidence_quote just to satisfy the request_fix marker
    gate. Short quotes are handled by the caller (too generic to verify); the deterministic ownership
    routing is the backstop regardless."""
    def _norm(s: str) -> str:
        return re.sub(r"\s+", " ", s or "").lower()

    parts = []
    for m in messages:
        c = getattr(m, "content", "")
        parts.append(c if isinstance(c, str) else str(c))
    return _norm(quote) in _norm(" ".join(parts))


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
    #   so the LLM can confirm the CI outcome. Persisting the validated fix as institutional
    #   knowledge is NOT an LLM tool — it is done deterministically in Python after a green
    #   verification (see end of this function). Exposing store_architectural_insight to the
    #   LLM caused a recursion loop: gpt-4o-mini mis-called it (wrong fields → pydantic error),
    #   retried, and burned turns instead of finishing a successful run.
    tools = [request_fix, query_vector_store]

    if state.get("infra_status") == "completed":
        tools.append(fetch_github_action_logs)
        logger.info("🔓 VERIFICATION phase: GitHub Logs UNLOCKED (insight stored in Python on green)")
    else:
        logger.info("🔒 DIAGNOSIS phase: CI log fetch locked until infra completes")

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
    # Smart standard injection: identify WHICH standard is relevant to the current error.
    # LLM-owned-artifact standards come from collected_specs (loaded by architect/infra);
    # CODE-OWNED-artifact standards (k8s/grafana/dockerfile/cicd — no longer retrieved at
    # generation time) are fetched from Pinecone here, deterministically. Only the relevant
    # one(s) are injected — no context bloat, no redundant queries.
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

    matched_standards = _resolve_relevant_standards(
        error_context, _STANDARD_INDICATORS, collected_specs,
        fetch=lambda q: query_vector_store.invoke({"query": q}),
    )

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
    # A locked tfstate (DynamoDB on AWS, GCS lock on GCP, blob lease on Azure) cannot be
    # resolved by any artifact change, so request_fix
    # would loop forever on no-op patches. Detect the marker execute_terraform emits and
    # short-circuit BEFORE the LLM runs: surface actionable guidance and FINISH (via
    # fix_loop_escalated, which the Supervisor routes to FINISH).
    _recent_blob = " ".join(str(getattr(m, "content", "")) for m in state["messages"][-8:])
    if "STATE_LOCK_ERROR" in _recent_blob:
        logger.warning("Terraform STATE_LOCK_ERROR detected — operational, surfacing to user (no fix loop).")
        _lock_msg = (
            "Terraform could not acquire the state lock — the tfstate is locked by a "
            "previous (cancelled/killed) run, so NO code fix applies. Break the stale lock "
            "and re-run. The universal fix works on every backend; the per-cloud notes "
            "remove the same stale lock at its source:\n"
            "  • Universal (any backend): from terraform/, run `terraform force-unlock <LOCK_ID>`\n"
            "  • AWS — delete the stale lock item from the DynamoDB lock table (terraform-state-lock)\n"
            "  • GCP — remove the stale lock on the GCS state object (delete the .tflock)\n"
            "  • Azure — break the tfstate blob lease: az storage blob lease break "
            "--account-name <tfstate-account> --container-name tfstate --blob-name <state-key> "
            "(or Portal → the tfstate blob → Break lease)"
        )
        return {
            "messages": [HumanMessage(content=_lock_msg)],
            "next_step": "supervisor",
            "last_agent": "medic",
            "healing_context": "",
            "fix_loop_escalated": True,   # Supervisor → FINISH; do not re-route a doomed fix
            "mission_status": "escalated",  # terminal: operational blocker, entry points fail the run
            "fix_attempt": 0,
            "last_fix_signature": "",
            "medic_fix_target": "",
        }

    # 3a. DETERMINISTIC CI POLL (verification phase). The green/pending outcome is mechanically
    # determined by the CI run state, so do NOT leave it to the LLM to (re-)call
    # fetch_github_action_logs. gpt-4o-mini frequently SKIPS the re-call on a re-poll turn — it
    # follows the prompt's "tell the user you are waiting and finish your turn" instead — so the
    # Python poll loop stalls, the supervisor falls to its LLM fallback, and the run FINISHes with
    # mission_status unset (MissionFailedError) on an otherwise-successful deploy. Masked locally
    # (deploy usually green by the first poll); it surfaces when the deploy workflow is still
    # QUEUED/in_progress on the first poll. Fetch here, in Python: green → verified, pending →
    # re-poll, only a REAL failure falls through to the LLM (whose job is DIAGNOSIS, not polling).
    # NOTE: the earlier 404 that forced the revert (b380eab→6e18143) is now fixed at the SOURCE —
    # fetch_github_action_logs normalises a timestamped PROJECT_ID to the bare pipeline name
    # (tools.py:_normalise), so passing project_id here resolves the correct
    # '<pipeline>_pipeline.yml'. FAIL-SAFE: the block is wrapped so it can NEVER crash the engine —
    # on ANY error it logs and falls through to the LLM-driven path (purely additive).
    _verif_tool = next((t for t in tools if t.name == "fetch_github_action_logs"), None)
    _push_sha = state.get("last_push_sha", "")
    if _verif_tool is not None and _push_sha:
        try:
            _poll = str(_verif_tool.invoke({"project_id": project_id, "head_sha": _push_sha}))
            _poll_msg = HumanMessage(content=f"[auto CI poll] {_poll}")
            messages.append(_poll_msg)
            new_messages_for_state.append(_poll_msg)
            if ("no failed jobs found" in _poll.lower()
                    or "everything looks green" in _poll.lower()):
                verification_successful = True
            elif ("PENDING" in _poll.upper() or "PERMISSIONS_ERROR" in _poll.upper()
                  or "could not list workflow runs" in _poll.lower()
                  or "error resolving run" in _poll.lower()):
                logs_still_pending = True
        except Exception as _poll_err:
            logger.warning(
                f"Auto CI poll failed ({_poll_err}); falling back to LLM-driven verification.")
        # else: a real CI failure — fall through to the reasoning loop for diagnosis / request_fix.

    # 3. REASONING LOOP
    fix_requested = False
    fix_signature_parts = []  # error text of each request_fix this turn → loop-convergence signature
    healing_context = ""  # Populated when request_fix is called; written to state for next agent
    for i in range(5):
        # Stop once the CI run is confirmed green OR still pending — BOTH are set deterministically
        # by the auto-poll above, so do NOT invoke the LLM (it could hallucinate a request_fix on an
        # already-verified run, or — the bug we are fixing — skip the re-fetch on a pending one and
        # FINISH unverified). The LLM runs ONLY for a REAL failure (both flags False) to diagnose.
        if verification_successful or logs_still_pending:
            break
        response = llm_with_tools.invoke(messages)
        messages.append(response)
        new_messages_for_state.append(response)

        if not response.tool_calls:
            # The LLM declaring "VERIFIED"/"COMPLIANT" in prose is NOT evidence — that is exactly
            # the hallucination to avoid (e.g. after a 404 / run-not-found it says "verified" and a
            # FAILED deploy is marked green → false success). Verification comes ONLY from the
            # deterministic green-CI fetch below. No tool call → nothing more to do; stop.
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

            # PENDING / not-yet-resolvable → back off and retry (NOT a failure, NOT success). A
            # "run not found" 404 right after a push is usually the deploy run not registered yet;
            # treat it as pending so we retry, and if it never resolves the poll limit ends the run
            # as ci_unverified (fail-closed) — never a false green.
            if tool_name == "fetch_github_action_logs" and (
                "PENDING" in result_str.upper()
                or "PERMISSIONS_ERROR" in result_str.upper()
                or "could not list workflow runs" in result_str.lower()
                or "error resolving run" in result_str.lower()
            ):
                logs_still_pending = True

            # CI green = verified, DETERMINISTICALLY (symmetric to the PENDING check above): the
            # success outcome must NOT depend on the LLM emitting "VERIFIED" as plain text after a
            # PENDING -> green poll dance (gpt-4o-mini often phrases it differently or re-polls,
            # leaving mission_status unset -> MissionFailedError on a SUCCESSFUL deploy).
            # fetch_github_action_logs returns this exact phrasing when no job failed.
            if tool_name == "fetch_github_action_logs" and (
                "no failed jobs found" in result_str.lower()
                or "everything looks green" in result_str.lower()
            ):
                verification_successful = True
                # Append THIS fetch's ToolMessage BEFORE breaking. Otherwise the medic returns an
                # AIMessage carrying an UNANSWERED tool_call, and the graph routes to the ToolNode
                # (EXECUTE_TOOLS) to satisfy it instead of to the supervisor — re-running the fetch,
                # hitting green, breaking before the ToolMessage again, and looping
                # medic↔EXECUTE_TOOLS forever. The supervisor (and its mission_status="verified"
                # FINISH) is never reached, so a SUCCESSFUL deploy dies at the recursion limit.
                t_msg = ToolMessage(tool_call_id=tool_call["id"], content=result_str)
                messages.append(t_msg)
                new_messages_for_state.append(t_msg)
                break  # green is conclusive — stop; the outer loop breaks before the next LLM call

            if tool_name == "request_fix" and '"REJECTED_BY_MEDIC"' in result_str:
                # Only ACCEPTED fix requests set routing flags. The request_fix tool rejects
                # calls whose evidence_quote carries no error marker (status TOOL_ERROR) —
                # e.g. the LLM hallucinating a "fix" for a CLEAN file, passing evidence
                # "AUTO-VALIDATION: CLEAN ✓". Honouring those would wrongly activate an agent
                # and pollute the convergence signature. Rejected calls fall through: the
                # ToolMessage is still appended below so the LLM sees the rejection and
                # self-corrects.
                #
                # PROVENANCE — the marker gate only checks the quote CONTAINS an error marker, not
                # that it is REAL. A quote ≥12 chars must also actually appear in a prior tool/message
                # output; otherwise it is a likely fabrication and we do not honour the routing (the
                # ToolMessage is still shown so the LLM self-corrects).
                # Search this-turn outputs TOO, not just state["messages"]: a verification-phase heal
                # quotes the CI log fetched by THIS turn's auto-poll / in-loop fetch, which lives only
                # in the local accumulator (node inputs are immutable, so state["messages"] never holds
                # anything produced this turn). Omitting it dropped every genuine CI-runtime heal.
                _evidence_pool = list(state["messages"]) + new_messages_for_state
                _quote = str(tool_args.get("evidence_quote", "")).strip()
                if len(_quote) >= 12 and not _evidence_has_provenance(_quote, _evidence_pool):
                    logger.warning(
                        f"request_fix evidence_quote not found in any real output — not honouring "
                        f"routing (possible hallucination): {_quote[:60]!r}"
                    )
                    t_msg = ToolMessage(
                        tool_call_id=tool_call["id"],
                        content=result_str + "\n[MEDIC: evidence_quote not found in real tool output — routing NOT honoured.]",
                    )
                    messages.append(t_msg)
                    new_messages_for_state.append(t_msg)
                    continue

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

    # DETERMINISTIC TERRAFORM-FAILURE OVERRIDE — a `terraform apply/plan` that FAILED
    # (execute_terraform → "FAILED: Terraform <cmd>") is an UNAMBIGUOUS infra bug: the fix is
    # terraform/main.tf, never a script. Detect it in Python and force an infra fix REGARDLESS of the
    # LLM's request_fix, which mis-routes it to the architect — either directly (target_agent), or
    # because the provenance gate first DROPS the paraphrased quote yet the rejected ToolMessage
    # echoes that quote back into the evidence pool, so the LLM's next retry "passes" provenance and
    # is honoured with target=architect. The architect cannot edit terraform (ownership guard), so the
    # fix loops architect↔medic to the recursion limit and the run CRASHES instead of healing. The
    # actual override (reset + target) is applied in the ownership routing below. Mirrors the
    # deterministic CI-poll: an unambiguous, Python-detectable failure must not depend on the LLM.
    _tf_fail_text = _terraform_command_failure(list(state["messages"]) + new_messages_for_state)
    _forced_tf_infra = bool(_tf_fail_text)
    if _forced_tf_infra:
        fix_requested = True  # ensure the fix cycle runs even if the LLM quote was dropped
        fix_signature_parts.append(_tf_fail_text[:200].lower())

    # 3b. DETERMINISTIC OWNERSHIP ROUTING — override the LLM's target_agent.
    # A file-validation failure belongs to exactly ONE agent. The LLM has mis-routed
    # the SAME .py error to BOTH architect and infra; honouring that drags infra into
    # patching files it doesn't own and regenerating unrelated artifacts (broken CI
    # workflow pushed to the repo). Derive the reset flags from the AUTHORITATIVE
    # FAILED-file list instead of trusting the LLM. (Skipped for CI-log failures,
    # which produce no validate_generated_code results — those keep the LLM's target.)
    deterministic_fix_target = ""  # written to state so the Supervisor routes by ownership
    if fix_requested:
        # CI logs fetched THIS turn (auto-poll / in-loop fetch) live only in the local accumulator —
        # node inputs are immutable, so state["messages"] never holds them within this invocation. The
        # ownership derivation MUST search both, or a CI-runtime failure's file/signature is invisible
        # and the fix is mis-routed (infra CI errors fell through to the architect default).
        _all_msgs = list(state["messages"]) + new_messages_for_state
        _failed_files = [f for f, (st, _) in _validation_results.items() if st == "FAILED"]
        if not _failed_files:
            # Architect auto-validation failures never reach _validation_results (validate
            # runs in Python, not as an LLM tool call). Recover the SINGLE most-recently
            # failed file from the messages so the architect patches EXACTLY that file —
            # not every artifact it generated. (Prevents over-patching a correct SQL/dashboard.)
            _latest_failed = _latest_autovalidation_failure(_all_msgs)
            _failed_files = [_latest_failed] if _latest_failed else []
        if _forced_tf_infra and not any(_owner_of_file(f) == "architect" for f in _failed_files):
            # Terraform apply/plan failure (detected in Python above). Route to infra deterministically,
            # OVERRIDING the LLM target, the provenance gate, and any architect reset the (wrong)
            # request_fix triggered. Guarded by "no architect-owned file also failed this turn" so a
            # genuine data-plane bug still heals first. Replace the LLM's architect-aimed diagnosis with
            # the terraform-targeted instruction so the infra agent patches terraform/main.tf.
            deterministic_fix_target = "infra"
            reset_infra = True
            reset_architect = False
            healing_context = (
                "Target: the pipeline Terraform (terraform/main.tf) — `terraform apply` FAILED (error "
                "below). This is an infra/provisioning bug, NOT a script bug. Fix the offending "
                "resource in terraform/main.tf, then re-run execute_terraform apply. Do NOT edit "
                "scripts/*.py, sql/*.sql, or dashboards.\n\n" + _tf_fail_text
            )
            logger.info("🧭 Ownership routing: terraform apply/plan failure → infra (overriding LLM target).")
        elif _failed_files:
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
        elif (_ci_owner := _ci_error_owner(_all_msgs)) == "infra":
            # CI-LOG failure with an UNAMBIGUOUS infra/dependency signature (e.g. a missing JDBC
            # driver → ClassNotFoundException). The error surfaces AT the script's read line, but
            # the fix is the Terraform job `library` block, NOT the script — so route to infra
            # deterministically (overriding the LLM's target_agent and the script-frame heuristic)
            # and point healing_context at the Terraform. Mirrors the generation-phase ownership
            # routing, but keyed on the ERROR TYPE rather than the file location.
            deterministic_fix_target = "infra"
            _ci_file = _extract_ci_failed_file(_all_msgs)
            # For a secret-key mismatch, compute the EXACT one-line patch (read the current key line
            # from the file) so the infra agent copies it verbatim instead of guessing — it kept
            # patching the `scope`/`name` (named in the error) and missing the actual `key` line.
            _exact = _databricks_secret_key_exact_fix(_all_msgs)
            _exact_block = ""
            if _exact:
                _old, _new = _exact
                _exact_block = (
                    "\n\n🎯 EXACT FIX — apply this SINGLE replacement to terraform/main.tf VERBATIM, and "
                    "change NOTHING else (do NOT touch the scope, the name, or any reference — ONLY this "
                    "one line):\n"
                    f"  old: {_old}\n"
                    f"  new: {_new}\n"
                    "Call patch_project_file with exactly this one old/new pair and then deploy.\n"
                )
            healing_context = (
                "Target: the pipeline Terraform (terraform/main.tf) — this is a missing PROVISIONED-"
                "RESOURCE runtime failure (see the error below), NOT a script bug. Fix it in the "
                "Terraform:\n"
                "  • ClassNotFoundException / NoClassDefFoundError → add/correct the `databricks_job` "
                "`library { maven { coordinates = ... } }` (the source JDBC driver);\n"
                "  • 'Secret does not exist … key: <k>' → the `databricks_secret` `key` must EXACTLY "
                "match the key the script reads in `dbutils.secrets.get(scope, \"<k>\")` — set it to "
                "the key named in the error (NOT the scope or the name).\n"
                "Do NOT edit the Spark script" + (f" `{_ci_file}`" if _ci_file else "") + " — it is correct."
                + _exact_block + "\n\n"
                + healing_context
            )
            logger.info("🧭 CI-runtime infra signature (missing library/secret) → routing fix to infra (Terraform).")
        else:
            # CI-LOG failure (a runtime error surfaced by fetch_github_action_logs) — there is no
            # validate_generated_code result to map to a file. A SCRIPT-LOGIC bug routes to the
            # architect DETERMINISTICALLY (no longer just via the LLM's target_agent); an
            # undetermined error ('') keeps the LLM target. Either way, pull the failing artifact
            # from the CI traceback so the architect can target it; otherwise healing_context names
            # no file, the architect rejects its own patch ("not the fix target"), and the fix loops.
            if _ci_owner == "architect":
                deterministic_fix_target = "architect"
            _ci_file = _extract_ci_failed_file(_all_msgs)
            if (_ci_file and healing_context
                    and Path(_ci_file).stem.lower() not in healing_context.lower()):
                healing_context = "Target file to fix: " + _ci_file + "\n\n" + healing_context
                logger.info(f"🩹 Injected CI-log failing file '{_ci_file}' into healing_context.")

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
    #
    # HARD TOTAL CAP (backstop): the identical-signature counter resets on ANY change in the error
    # text, so a FLAILING heal — an agent that patches a structural bug into a DIFFERENT error each
    # round (e.g. terraform "Insufficient block" → "Missing status" → "Unsupported enabled" → …) —
    # slips past it and loops to the graph recursion_limit / an LLM timeout (a CRASH, not a clean
    # stop). total_fix_attempts counts EVERY fix round regardless of signature and escalates the run
    # cleanly (fail-closed, with the diagnosis) once the churn is clearly not converging.
    _MAX_FIX_ATTEMPTS = 3        # SAME error repeated → not converging
    _MAX_TOTAL_FIX_ATTEMPTS = 8  # ANY heal churn (even with shifting errors) → hard backstop
    escalated_fix_loop = False
    if fix_requested:
        total_fix_attempts = state.get("total_fix_attempts", 0) + 1
        output_state["total_fix_attempts"] = total_fix_attempts
        current_sig = hashlib.sha256(
            "||".join(sorted(filter(None, (_normalize_error_sig(p) for p in fix_signature_parts)))).encode()
        ).hexdigest()
        if current_sig and current_sig == state.get("last_fix_signature", ""):
            fix_attempt = state.get("fix_attempt", 0) + 1  # same error again → not converging
        else:
            fix_attempt = 1  # new/different error → progress, restart the count

        _same_error = fix_attempt >= _MAX_FIX_ATTEMPTS
        _flailing = total_fix_attempts >= _MAX_TOTAL_FIX_ATTEMPTS
        if _same_error or _flailing:
            _why = (
                f"the same error survived {fix_attempt} attempts" if _same_error else
                f"the heal churned through {total_fix_attempts} rounds without converging (the error "
                f"keeps changing shape — the surgical patch cannot resolve it)"
            )
            logger.warning(f"Fix loop not converging: {_why}. Stopping self-heal and surfacing to the user.")
            new_messages_for_state.append(HumanMessage(content=(
                f"Self-healing could not resolve this — {_why}. The automated fix is not converging; "
                f"a manual edit to the standard/prompt is likely needed. Last diagnosis:\n\n{healing_context}"
            )))
            output_state["messages"] = new_messages_for_state
            output_state["next_step"] = "supervisor"
            output_state["fix_attempt"] = 0
            output_state["last_fix_signature"] = ""
            output_state["total_fix_attempts"] = 0
            output_state["healing_context"] = ""  # do NOT route the doomed fix to an agent
            output_state["medic_fix_target"] = ""  # cancel any pending ownership route
            # Explicit flag the supervisor honours — without it, supervisor RULE C would
            # re-derive the fix target from the request_fix message still in history and
            # route back to the architect, defeating this guard.
            output_state["fix_loop_escalated"] = True
            output_state["mission_status"] = "escalated"  # terminal: entry points fail the run
            escalated_fix_loop = True
        else:
            output_state["fix_attempt"] = fix_attempt
            output_state["last_fix_signature"] = current_sig
    elif verification_successful:
        # Clean verification — reset so the next pipeline's fix cycle starts fresh.
        output_state["fix_attempt"] = 0
        output_state["last_fix_signature"] = ""
        output_state["total_fix_attempts"] = 0
        output_state["mission_status"] = "verified"  # the ONLY terminal success signal

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
    if logs_still_pending and not verification_successful and not reset_infra and not reset_architect:
        attempt = state.get("ci_poll_attempt", 0)

        if _should_stop_polling(attempt):
            # Exceeded ~10 minutes of cumulative waiting — TERMINAL. The deployment outcome
            # is UNKNOWN, so this must end the run as a failure, deterministically. Before
            # this was terminal, the supervisor's LLM fallback could route back to MEDIC
            # with the counter freshly reset — re-polling for another ~17 minutes per cycle
            # until the recursion limit (hours of sleeps in CI).
            logger.warning("CI run has been pending for over 10 minutes. Terminal: deployment unverified.")
            new_messages_for_state.append(
                HumanMessage(content=(
                    "CI run has been pending for over 10 minutes with no result. "
                    "Possible causes: workflow file not pushed, wrong trigger branch, GHA disabled, "
                    "or a GH_TOKEN permissions error (403) on log fetch. "
                    "The deployment is UNVERIFIED — please check GitHub Actions manually."
                ))
            )
            output_state["messages"] = new_messages_for_state
            output_state["ci_poll_attempt"] = 0  # reset for next pipeline run
            output_state["next_step"] = "supervisor"
            output_state["fix_loop_escalated"] = True       # supervisor routes FINISH deterministically
            output_state["mission_status"] = "ci_unverified"
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

            # The tool signature is (error_summary, solution, cloud_provider) — passing
            # {"insight", "project_id"} raises 3 pydantic "Field required" errors and the
            # insight is silently dropped by the except below. Map to the real fields.
            store_architectural_insight.invoke({
                "error_summary": f"Pipeline '{pipeline_id}' on '{cloud}' deployed and verified end-to-end.",
                "solution": f"Verified artifacts: {', '.join(written) or 'n/a'}. CI/CD passed, no errors detected.",
                "cloud_provider": cloud,
            })
            logger.info("💾 Architectural insight stored after successful verification.")
        except Exception as e:
            logger.warning(f"Failed to store architectural insight: {e}")

    return output_state
