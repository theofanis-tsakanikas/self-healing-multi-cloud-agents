import io
import os
import re
import shlex
import shutil
import urllib.error
import urllib.request
import zipfile
from langchain_core.tools import tool
import sqlite3
import pandas as pd
import json
from faker import Faker
import subprocess
from pathlib import Path
from dotenv import load_dotenv
import yaml
from sqlalchemy import create_engine, inspect, text
from openai import OpenAI
from pinecone import Pinecone
import time
import logging
from langchain_openai import OpenAIEmbeddings
from utils.cloud_config import cloud_get
from agents.constants import K8S_PINNED_IMAGES

# Initialize Pinecone client
try:
    pc = Pinecone(api_key=os.getenv("PINECONE_API_KEY"))
    index_name = os.getenv("PINECONE_INDEX_NAME")
    index = pc.Index(index_name)
except Exception as _pinecone_err:
    print(f"WARNING: Pinecone initialization failed: {_pinecone_err}. Check PINECONE_API_KEY and PINECONE_INDEX_NAME.")
    pc = None
    index_name = None
    index = None

# Define the logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables from .env
load_dotenv()

# Setting stable base paths (independent of runtime cwd)
TOOLS_FILE = Path(__file__).resolve()
PROJECT_ROOT = TOOLS_FILE.parent.parent

def _find_git_root(start: Path) -> Path:
    """
    Walk up from `start` until a .git directory is found.
    Returns that directory as the repository root.
    Falls back to `start` if no .git is found (git commands will fail
    with a clear 'not a git repository' error rather than a cryptic exit-128).
    """
    current = start.resolve()
    while current != current.parent:
        if (current / ".git").exists():
            return current
        current = current.parent
    return start  # fallback: no .git found

REPO_ROOT = _find_git_root(PROJECT_ROOT)

# --- INITIALIZE CLIENTS ---
try:
    client = OpenAI()
    embeddings_model = OpenAIEmbeddings(model="text-embedding-3-small")
except Exception as _openai_err:
    print(f"WARNING: OpenAI client initialization failed: {_openai_err}. Check OPENAI_API_KEY.")
    client = None
    embeddings_model = None

# --- HELPERS (Not tools) ---
def get_embedding(text):
    """Generates embeddings using OpenAI's text-embedding-3-small model."""
    try:
        if client is None:
            return None
        if not text:
            return None
        text = text.replace("\n", " ")
        return client.embeddings.create(input=[text], model="text-embedding-3-small").data[0].embedding
    except Exception as e:
        print(f"Error calling OpenAI Embedding API: {e}")
        return None

    
@tool
def validate_generated_code(filename: str) -> str:
    """
    Validates generated artifacts using real linting tools + minimal project-specific policy checks.

    Tool chain per file type:
    - .py            → ruff + py_compile (syntax, undefined names, missing imports)
    - .json          → json.loads + Grafana mandatory fields
    - .sql           → structural checks (no standard linter exists for Trino DDL)
    - requirements.txt → mandatory package presence
    - Dockerfile     → hadolint (general best practices) + COPY utils/ project policy
    - .yaml / .yml   → kubectl apply --dry-run=client (K8s schema) + project policy checks

    Project-specific checks cover only what linting tools cannot know:
    our architecture's required ConfigMaps, env vars, and security policies.
    All general best practices (image pinning style, non-root user, etc.) are
    delegated to hadolint / kubectl — not duplicated here.

    Returns 'CLEAN' or a list of errors to fix before proceeding.
    """
    import py_compile
    import json as _json

    if not os.path.exists(filename):
        return f"Error: file '{filename}' does not exist. Did write_project_file succeed?"

    errors = []
    warnings = []  # non-blocking: missing optional tools, env notes
    ext = Path(filename).suffix.lower()
    base = Path(filename).name.lower()

    # ── Python ────────────────────────────────────────────────────────────────
    if ext == ".py":
        try:
            py_compile.compile(filename, doraise=True)
        except py_compile.PyCompileError as e:
            errors.append(f"SYNTAX ERROR:\n{e}")

        ruff_path = shutil.which("ruff")
        if ruff_path:
            result = subprocess.run(
                [ruff_path, "check", "--select", "F,E9", "--extend-ignore", "F401", "--no-cache", filename],
                capture_output=True, text=True
            )
            if result.returncode != 0 and result.stdout.strip():
                errors.append(f"RUFF:\n{result.stdout.strip()}")
        else:
            warnings.append("ruff not installed — only py_compile ran (syntax check only).")

        # Project policy: cloud_get() is MANDATORY for all DB credentials.
        # os.getenv() bypasses SSM and .bootstrap_outputs.json, breaking production.
        # Infrastructure endpoints (TRINO_HOST, PUSHGATEWAY_URL etc.) are exempt.
        #
        # Keys are GENERIC (db_host, db_port, db_user, db_password, db_name) —
        # the same API regardless of cloud provider or DB engine.
        # cloud_config.py handles the env-var fallback for each combination.
        _CRED_ENVVARS = frozenset({
            "POSTGRES_DB_HOST", "POSTGRES_DB_PORT", "POSTGRES_DB_USER",
            "POSTGRES_DB_PASSWORD", "POSTGRES_DB_NAME",
            "MYSQL_DB_HOST", "MYSQL_DB_PORT", "MYSQL_DB_USER",
            "MYSQL_DB_PASSWORD", "MYSQL_DB_NAME",
        })
        # env-var name → generic cloud_get() key (same for every provider/engine)
        _ENVVAR_TO_GENERIC_KEY = {
            "POSTGRES_DB_HOST":     "db_host",
            "POSTGRES_DB_PORT":     "db_port",
            "POSTGRES_DB_USER":     "db_user",
            "POSTGRES_DB_PASSWORD": "db_password",
            "POSTGRES_DB_NAME":     "db_name",
            "MYSQL_DB_HOST":        "db_host",
            "MYSQL_DB_PORT":        "db_port",
            "MYSQL_DB_USER":        "db_user",
            "MYSQL_DB_PASSWORD":    "db_password",
            "MYSQL_DB_NAME":        "db_name",
        }

        with open(filename, encoding="utf-8") as f:
            py_content = f.read()

        # Detect the cloud provider declared in the file.
        # Matches: _CLOUD = os.getenv("CLOUD_PROVIDER", "aws") or _CLOUD = "gcp"
        _cloud_detect = re.search(
            r'(?:_CLOUD|CLOUD_PROVIDER)\s*=\s*[^\n]*["\'](\w+)["\']',
            py_content,
        )
        detected_provider = _cloud_detect.group(1).lower() if _cloud_detect else "aws"

        getenv_scan = re.compile(r'os\.getenv\s*\(\s*["\']([^"\']+)["\'][^)]*\)', re.IGNORECASE)
        found_cred_violations = [
            (m.group(1).upper(), m.group(0))
            for m in getenv_scan.finditer(py_content)
            if m.group(1).upper() in _CRED_ENVVARS
        ]
        # Detect the db_type from env var names found (POSTGRES_* → postgres, MYSQL_* → mysql)
        detected_db_type = "mysql" if any(
            ev.startswith("MYSQL_") for ev, _ in found_cred_violations
        ) else "postgres"

        if found_cred_violations:
            replacements = "\n".join(
                f'  {original}  →  cloud_get("{detected_provider}", "{_ENVVAR_TO_GENERIC_KEY.get(env_var, "db_host")}", db_type="{detected_db_type}")'
                for env_var, original in found_cred_violations
            )
            errors.append(
                f'POLICY VIOLATION — os.getenv() used for DB credentials '
                f'(detected provider: "{detected_provider}", db_type: "{detected_db_type}").\n'
                "Apply these EXACT replacements:\n"
                f"{replacements}\n"
                "Also add this import at the top of the file:\n"
                "  from utils.cloud_config import cloud_get\n"
                "cloud_get() reads SSM → .bootstrap_outputs.json → env fallback (production-safe)."
            )

        # storage_options={} is required for every to_parquet() call that writes to
        # cloud storage (s3://, gs://, abfss://). Omitting it causes TypeError at
        # runtime — pyarrow falls back to local filesystem and rejects the URI scheme.
        if "to_parquet(" in py_content and "storage_options" not in py_content:
            errors.append(
                "STORAGE: to_parquet() call found but storage_options={} is missing — "
                "add storage_options={} to every to_parquet() call. "
                "Without it, pyarrow cannot write to s3://, gs://, or abfss:// URIs."
            )

        # destination_uri must come from os.getenv("DESTINATION_URI") — the K8s Job
        # injects it at runtime. A hardcoded URI string makes the script un-deployable
        # to a different bucket without a code change.
        _hardcoded_uri = re.search(
            r'destination_uri\s*=\s*["\'](?:s3://|gs://|abfss://)[^"\']+["\']',
            py_content,
        )
        if _hardcoded_uri:
            errors.append(
                "STORAGE: destination_uri is hardcoded as a URI literal — "
                "replace with: destination_uri = os.getenv(\"DESTINATION_URI\")  "
                "The K8s Job env block injects this at runtime."
            )

        # chunk['is_suspicious'] = False is a compliance violation — it is a placeholder,
        # not an implementation of FLAG_AS_SUSPICIOUS. Every quality_standards rule must
        # be real pandas code: chunk['is_suspicious'] = ~condition.
        if re.search(r"chunk\[.is_suspicious.\]\s*=\s*False", py_content):
            errors.append(
                "BUSINESS RULES: chunk['is_suspicious'] = False is a placeholder — "
                "COMPLIANCE VIOLATION. See python_standards.md Business Rules section."
            )

        # Cloud guard: cloud_get() for a specific cloud must be inside the matching
        # if _CLOUD == "..." block. An unguarded call hardcodes a provider and breaks
        # the script on all other clouds — a fundamental cloud-agnostic violation.
        for _cld in ("aws", "gcp", "azure"):
            if f'cloud_get("{_cld}",' in py_content and f'if _CLOUD == "{_cld}"' not in py_content:
                errors.append(
                    f'CLOUD GUARD: cloud_get("{_cld}", ...) is called without an '
                    f'"if _CLOUD == \\"{_cld}\\":" guard — the script uses {_cld.upper()} '
                    f"credentials unconditionally, breaking deployment on other clouds. "
                    f"Wrap all cloud_get() calls and the connection_string in cloud-specific "
                    f"if/elif blocks as shown in arch_standard_python Section 2 skeleton."
                )

    # ── JSON (Grafana dashboard) ──────────────────────────────────────────────
    elif ext == ".json":
        try:
            with open(filename, encoding="utf-8") as f:
                data = _json.load(f)
            missing = [k for k in ("uid", "title", "schemaVersion", "panels") if k not in data]
            if missing:
                errors.append(f"GRAFANA: missing mandatory fields: {missing}")
            if not isinstance(data.get("panels"), list) or len(data.get("panels", [])) == 0:
                errors.append("GRAFANA: 'panels' must be a non-empty list.")
        except _json.JSONDecodeError as e:
            errors.append(f"JSON SYNTAX ERROR: {e}")

    # ── SQL (Trino DDL) ───────────────────────────────────────────────────────
    elif ext == ".sql":
        with open(filename, encoding="utf-8") as f:
            content = f.read().upper()
        if "CREATE TABLE" not in content:
            errors.append("SQL: missing CREATE TABLE statement.")
        if "EXTERNAL_LOCATION" not in content:
            errors.append("SQL: missing EXTERNAL_LOCATION in WITH clause.")
        if "PARTITIONED_BY" not in content:
            errors.append("SQL: missing PARTITIONED_BY = ARRAY['run_date'].")
        if "FORMAT" not in content:
            errors.append("SQL: missing FORMAT = 'PARQUET' in WITH clause.")
        if "CREATE EXTERNAL TABLE" in content:
            errors.append("SQL: 'CREATE EXTERNAL TABLE' is Hive/HQL syntax — use plain 'CREATE TABLE' in Trino.")
        if "S3A://" in content:
            errors.append("SQL: s3a:// is Hadoop/Spark only — Trino uses s3:// (AWS), gs:// (GCP), abfss:// (Azure).")
        cloud = os.getenv("CLOUD_PROVIDER", "").lower()
        if cloud == "aws" and ("GS://" in content or "ABFSS://" in content):
            errors.append("SQL: GCS/Azure protocol in an AWS pipeline — use s3://.")
        elif cloud == "gcp" and ("S3://" in content or "ABFSS://" in content):
            errors.append("SQL: S3/Azure protocol in a GCP pipeline — use gs://.")
        elif cloud == "azure" and ("S3://" in content or "GS://" in content):
            errors.append("SQL: S3/GCS protocol in an Azure pipeline — use abfss://.")

    # ── requirements.txt ─────────────────────────────────────────────────────
    elif base == "requirements.txt":
        with open(filename, encoding="utf-8") as f:
            lines = [l.strip() for l in f if l.strip() and not l.strip().startswith("#")]
        mandatory = ["pandas", "sqlalchemy", "pyarrow", "trino", "prometheus-client"]
        missing = [p for p in mandatory if not any(p in l.lower() for l in lines)]
        if missing:
            errors.append(f"REQUIREMENTS: missing mandatory packages: {missing}")
        # Cloud-specific filesystem driver for to_parquet() — omitting causes silent S3/GCS/ADLS failures.
        content_lower = " ".join(lines).lower()
        if "boto3" in content_lower and "s3fs" not in content_lower:
            errors.append("REQUIREMENTS: boto3 present but s3fs missing — add 's3fs' to requirements.txt (repo root, not scripts/).")
        if "google-cloud-storage" in content_lower and "gcsfs" not in content_lower:
            errors.append("REQUIREMENTS: google-cloud-storage present but gcsfs missing — add 'gcsfs' to requirements.txt (repo root, not scripts/).")
        if "azure-storage-blob" in content_lower and "adlfs" not in content_lower:
            errors.append("REQUIREMENTS: azure-storage-blob present but adlfs missing — add 'adlfs' to requirements.txt (repo root, not scripts/).")

    # ── Terraform ────────────────────────────────────────────────────────────
    elif ext == ".tf":
        with open(filename, encoding="utf-8") as f:
            tf_content = f.read()

        if base == "main.tf":
            # IAM least privilege: the pipeline writes only to {bucket}/processed/run_date=.../
            # so object-level actions must be scoped to /processed/*, not the full bucket (/*).
            # Pattern matches: "${aws_s3_bucket.data_bucket.arn}/*" — arn ref followed directly by /*"
            if re.search(r'\.arn\}\s*/\*"', tf_content):
                errors.append(
                    "IAM [project policy]: GetObject/PutObject/DeleteObject resource is '/*' (full bucket) — "
                    "restrict to '/processed/*'. The pipeline only writes to processed/run_date=.../. "
                    "See terraform_aws_s3.md Section 3."
                )
            # AWS: Glue permissions are mandatory — Trino uses Glue Data Catalog as metastore.
            # Without them, CREATE TABLE, DROP TABLE, and CALL sync_partition_metadata all fail.
            # Azure and GCP use file-based metastore on their storage, covered by existing permissions.
            _tf_cloud = os.getenv("CLOUD_PROVIDER", "").lower()
            if _tf_cloud == "aws" and "glue:GetTable" not in tf_content:
                errors.append(
                    "IAM [project policy]: AWS Glue permissions missing from IAM policy — "
                    "Trino uses Glue Data Catalog as metastore; glue:GetTable, glue:CreateTable, "
                    "glue:BatchCreatePartition etc. are required for CREATE TABLE and sync_partition_metadata. "
                    "See terraform_aws_s3.md Section 3."
                )

    # ── Dockerfile ───────────────────────────────────────────────────────────
    # hadolint covers: base image tag, COPY . ., non-root user, pip flags, layer hygiene.
    # We add only the ONE rule hadolint cannot know: our project requires utils/.
    elif base == "dockerfile":
        hadolint = shutil.which("hadolint")
        if hadolint:
            result = subprocess.run(
                [hadolint, filename],
                capture_output=True, text=True
            )
            output = (result.stdout + result.stderr).strip()
            if output:
                errors.append(f"HADOLINT:\n{output}")
        else:
            warnings.append(
                "hadolint not installed — Dockerfile best-practice lint skipped. "
                "Install: brew install hadolint (macOS) or apt-get install hadolint (Linux)."
            )

        # Project-specific: utils/ is OUR module tree — hadolint cannot know this is required.
        with open(filename, encoding="utf-8") as f:
            dockerfile_content = f.read()
        if "copy utils/" not in dockerfile_content.lower():
            # Find an anchor line for the patch suggestion.
            anchor = None
            for candidate in ("COPY scripts/ scripts/", "COPY scripts/", "CMD ", "ENTRYPOINT "):
                if candidate.lower() in dockerfile_content.lower():
                    for line in dockerfile_content.splitlines():
                        if line.strip().lower().startswith(candidate.lower()):
                            anchor = line.strip()
                            break
                if anchor:
                    break
            patch_hint = (
                f"\nFix: add 'COPY utils/ utils/' immediately before '{anchor}'."
                if anchor else
                "\nFix: add 'COPY utils/ utils/' before the CMD/ENTRYPOINT instruction."
            )
            errors.append(
                "DOCKERFILE [project policy]: missing 'COPY utils/ utils/' — "
                "pipeline scripts import 'from utils.cloud_config import cloud_get'. "
                f"Omitting this causes ModuleNotFoundError at container startup.{patch_hint}"
            )

    # ── YAML files — two distinct types require different validation ─────────────
    elif ext in (".yaml", ".yml"):
        import re as _re

        with open(filename, encoding="utf-8") as f:
            raw = f.read()
        content_upper = raw.upper()
        content_lower = raw.lower()
        fpath = filename.replace("\\", "/").lower()
        fname = Path(filename).name.lower()

        # Detect GitHub Actions workflows by path — they must NOT go through kubectl.
        is_gha_workflow = ".github/workflows" in fpath

        if is_gha_workflow:
            # ── GitHub Actions Workflow ───────────────────────────────────────
            # Parse YAML syntax, check structure, and catch unresolved placeholders.
            # kubectl --dry-run would reject these files (wrong resource type).
            try:
                yaml.safe_load(raw)
            except yaml.YAMLError as e:
                errors.append(f"GHA YAML SYNTAX ERROR:\n{e}")

            # Minimal structure check: every valid workflow needs 'on:' and 'jobs:'
            if "on:" not in raw and "\"on\":" not in raw:
                errors.append("GHA: missing 'on:' trigger — GitHub Actions workflow must define when it runs.")
            if "jobs:" not in raw:
                errors.append("GHA: missing 'jobs:' — workflow has no jobs defined.")

            # Unresolved placeholders in CI scripts cause silent failures or wrong deployments.
            placeholders = _re.findall(r"<[A-Za-z_][A-Za-z0-9_.]{2,}>", raw)
            if placeholders:
                errors.append(
                    f"GHA: unresolved placeholder(s) {list(set(placeholders))} — "
                    "replace every <...> token with its actual value from context "
                    "(e.g. <AWS_ACCOUNT_ID> → the 12-digit account ID from Terraform outputs)."
                )
            # Secret name in kubectl create must be RFC 1123 (lowercase + hyphens, no underscores).
            secret_creates = _re.findall(r"kubectl create secret generic\s+(\S+)", raw)
            bad_secret_names = [n for n in secret_creates if _re.search(r"[A-Z_]", n)]
            if bad_secret_names:
                errors.append(
                    f"GHA: kubectl secret name(s) {bad_secret_names} violate RFC 1123 — "
                    "K8s resource names must be lowercase with hyphens only (no underscores, no uppercase). "
                    "Must match the secretRef.name in job.yaml exactly."
                )

            # Standalone repo guard: paths must NOT use 'projects/<name>/' prefix.
            # This repo is standalone — all paths are relative to the repository root.
            monorepo_paths = list(set(_re.findall(r"projects/[a-zA-Z0-9_-]+/", raw)))
            if monorepo_paths:
                errors.append(
                    f"GHA [project policy]: monorepo path(s) detected {monorepo_paths} — "
                    "this is a STANDALONE repository. All paths are relative to the repo root:\n"
                    "  • Dockerfile  (not 'projects/.../Dockerfile')\n"
                    "  • k8s/job.yaml  (not 'projects/.../k8s/job.yaml')\n"
                    "  • docker build context is '.'  (not 'projects/...')\n"
                    "  • on.push.paths: omit or use '**'  (not 'projects/...')"
                )

        else:
            # ── Kubernetes Manifests ──────────────────────────────────────────
            # kubectl --dry-run=client validates the full K8s schema (apiVersion, kinds,
            # required fields, type mismatches) without touching the cluster.
            # We add only project-specific POLICY checks: architecture decisions that
            # kubectl cannot enforce (which ConfigMaps exist, which env vars the app needs, etc.)
            # kubectl --dry-run=client is NOT fully offline: it tries to download
            # the OpenAPI schema from the API server even in client mode.
            # If no cluster is reachable, we treat it as a non-blocking warning
            # (same class as "ruff not installed") — the code is not wrong,
            # the validation environment is limited.
            # Our policy checks below (placeholders, :latest, per-file rules) run
            # regardless and catch the real project-level errors.
            kubectl = shutil.which("kubectl")
            if kubectl:
                result = subprocess.run(
                    [kubectl, "apply", "--dry-run=client", "-f", filename],
                    capture_output=True, text=True
                )
                if result.returncode != 0:
                    stderr = result.stderr.strip()
                    # Distinguish environment issues (no cluster) from real schema errors
                    is_no_cluster = any(phrase in stderr for phrase in [
                        "connection refused",
                        "failed to download openapi",
                        "dial tcp",
                        "unable to connect to the server",
                        "no such host",
                    ])
                    if is_no_cluster:
                        warnings.append(
                            "kubectl dry-run skipped — no cluster reachable locally. "
                            "Schema validation will run in CI where a cluster context is configured."
                        )
                    else:
                        errors.append(f"KUBECTL DRY-RUN:\n{stderr}")
            else:
                warnings.append(
                    "kubectl not installed — K8s schema validation skipped. "
                    "Install kubectl to enable dry-run schema checks."
                )

            # ── Universal policy checks (apply to every K8s manifest) ─────────
            # Unresolved template placeholders break deployments silently.
            placeholders = _re.findall(r"<[A-Za-z_][A-Za-z0-9_.]{2,}>", raw)
            if placeholders:
                unique_ph = list(set(placeholders))
                hint = ""
                ecr_hint_triggers = {"<AWS_ACCOUNT_ID>", "<ECR_REPOSITORY_URL>", "<ECR_REPO_URL>"}
                if ecr_hint_triggers & set(unique_ph) or any(".ecr_" in p or "ecr_repo" in p.lower() for p in unique_ph):
                    hint = (
                        " For ECR image placeholders: use the full ECR repository URL "
                        "(e.g. 123456789012.dkr.ecr.eu-central-1.amazonaws.com/eu-sales-pipeline-repo) "
                        "from the execute_terraform output or the ecr_repository_url in the orchestration context."
                    )
                errors.append(
                    f"K8S: unresolved placeholder(s) {unique_ph} — "
                    f"replace every <...> token with its actual value from context before applying.{hint}"
                )

            # :latest tags are policy violations for public images.
            # ECR images (.dkr.ecr.amazonaws.com) are exempt — CI/CD patches the tag on
            # deployment via the commit SHA. Public images must use pinned versions.
            all_latest = _re.findall(r"image:\s*(\S+):latest", raw, _re.IGNORECASE)
            # Placeholder images (e.g. <ECR_REPOSITORY_URL>) are already caught by the
            # placeholder check above — exclude them from the :latest check to avoid
            # a confusing second error about the same token.
            public_latest = [img for img in all_latest if ".dkr.ecr." not in img and not img.startswith("<")]
            ecr_latest = [img for img in all_latest if ".dkr.ecr." in img]
            if public_latest:
                fixes = []
                for img in public_latest:
                    pinned = next((v for k, v in K8S_PINNED_IMAGES.items() if k in img), None)
                    if pinned:
                        fixes.append(f"  {img}:latest  →  {pinned}")
                    else:
                        fixes.append(f"  {img}:latest  →  pin to a specific version from k8s_deployment_rules.md")
                errors.append(
                    f"K8S: ':latest' image tag(s) found for public images — replace with pinned versions:\n"
                    + "\n".join(fixes)
                )
            if ecr_latest:
                warnings.append(
                    f"ECR image(s) {ecr_latest} use ':latest' — CI/CD will pin to commit SHA on deployment. Acceptable."
                )

            # ── Per-file project policy checks ────────────────────────────────
            if fname == "job.yaml":
                if "BACKOFFLIMIT:0" not in content_upper.replace(" ", ""):
                    errors.append("K8S job.yaml [project policy]: backoffLimit must be 0 — jobs are idempotent; retries mask failures.")
                if "ENVFROM" not in content_upper:
                    errors.append("K8S job.yaml [project policy]: missing envFrom: secretRef — DB credentials must be injected via K8s Secret, never in env[].")
                if not _re.search(r'namespace:\s*analytics', raw):
                    errors.append(
                        "K8S job.yaml [project policy]: missing 'namespace: analytics' in job metadata — "
                        "the Job must run in the analytics namespace where Trino and the ServiceAccount live."
                    )
                # Check env vars are present in the *pipeline* container specifically, not
                # just anywhere in the file. init-trino carries some vars too, which would
                # cause a raw string-search to give a false-CLEAN for the pipeline container.
                _pipeline_env_vars: set = set()
                try:
                    for _doc in yaml.safe_load_all(raw):
                        if not _doc:
                            continue
                        _pod_spec = (
                            _doc.get("spec", {})
                                .get("template", {})
                                .get("spec", {})
                        )
                        for _c in (_pod_spec.get("containers", []) or []):
                            if (_c.get("name") or "").lower() == "pipeline":
                                for _ev in (_c.get("env", []) or []):
                                    if _ev.get("name"):
                                        _pipeline_env_vars.add(_ev["name"])
                except yaml.YAMLError:
                    pass
                _required_pipeline_vars = [
                    "PROJECT_ID", "CLOUD_PROVIDER", "TRINO_HOST", "PUSHGATEWAY_URL", "DESTINATION_URI"
                ]
                if _pipeline_env_vars:
                    # YAML parsed successfully — check pipeline container directly
                    for env_var in _required_pipeline_vars:
                        if env_var not in _pipeline_env_vars:
                            errors.append(
                                f"K8S job.yaml [project policy]: missing env var '{env_var}' in the "
                                f"'pipeline' container — required by the pipeline script. "
                                f"(Having it only in the init-trino container is not enough.)"
                            )
                else:
                    # Fallback: YAML parse failed or pipeline container not found — broad check
                    for env_var in _required_pipeline_vars:
                        if env_var not in raw:
                            errors.append(f"K8S job.yaml [project policy]: missing env var '{env_var}' — required by the pipeline script.")
                if "PUSHGATEWAY_URL" in raw and not _re.search(r'http://pushgateway', raw):
                    errors.append(
                        "K8S job.yaml [project policy]: PUSHGATEWAY_URL value is missing the 'http://' scheme — "
                        "prometheus_client.push_to_gateway() requires a full URL. "
                        "Correct: value: 'http://pushgateway.monitoring.svc.cluster.local:9091'."
                    )
                trino_host_match = _re.search(r"TRINO_HOST[^\n]*value[^\n]*:(\d+)", raw)
                if trino_host_match:
                    errors.append(
                        "K8S job.yaml [project policy]: TRINO_HOST value must be hostname only "
                        "(e.g. trino.analytics.svc.cluster.local) — never include :port. "
                        "The pipeline script reads port separately."
                    )
                # initContainers must be separate from containers — LLM often puts init-trino in containers[].
                if "initcontainers" not in content_lower:
                    errors.append(
                        "K8S job.yaml [project policy]: missing 'initContainers' section — "
                        "init-trino MUST be under initContainers (runs before pipeline). "
                        "Using containers[] for it means both run in parallel and the schema setup is skipped."
                    )
                # Pipeline container (the Python script) must exist as a main container.
                if "name: pipeline" not in raw:
                    errors.append(
                        "K8S job.yaml [project policy]: missing 'name: pipeline' container — "
                        "the main container that runs the Python pipeline script is required under containers[]."
                    )
                # serviceAccountName is mandatory for workload identity (IRSA/GKE WI/Azure WI).
                if "serviceaccountname" not in content_lower:
                    errors.append(
                        "K8S job.yaml [project policy]: missing serviceAccountName — "
                        "required for cloud workload identity (IRSA on AWS, Workload Identity on GCP/Azure). "
                        "Without it the pipeline pod cannot access S3/GCS/ADLS."
                    )
                # restartPolicy must be Never — OnFailure is contradictory with backoffLimit=0.
                if "restartpolicy:never" not in content_lower.replace(" ", ""):
                    errors.append(
                        "K8S job.yaml [project policy]: restartPolicy must be Never — "
                        "OnFailure with backoffLimit=0 is contradictory and causes confusing behaviour."
                    )
                # RFC 1123: secretRef names must be lowercase with hyphens only.
                secret_names = _re.findall(r"secretRef:\s*\n\s*name:\s*(\S+)", raw)
                bad_secrets = [n for n in secret_names if _re.search(r"[A-Z_]", n)]
                if bad_secrets:
                    errors.append(
                        f"K8S job.yaml [project policy]: secretRef name(s) {bad_secrets} violate RFC 1123 — "
                        "K8s resource names must be lowercase with hyphens only. "
                        "Replace underscores with hyphens and convert to lowercase: "
                        "e.g. PIPE_EU_SALES_TO_S3-db-credentials → pipe-eu-sales-to-s3-db-credentials"
                    )

            elif fname == "configmaps.yaml":
                # These 5 names are our architecture — no tool knows they're all required.
                required_cms = [
                    "trino-sql-config", "hive-catalog-config",
                    "grafana-dash-config", "grafana-datasource-config", "prometheus-config"
                ]
                missing_cms = [cm for cm in required_cms if cm not in raw.lower()]
                if missing_cms:
                    errors.append(
                        f"K8S configmaps.yaml [project policy]: missing ConfigMap(s) {missing_cms} — "
                        "all 5 are required per k8s_deployment_rules.md Section 2."
                    )
                # Prometheus must scrape Pushgateway, not Trino — common LLM mistake.
                if "pushgateway.monitoring.svc.cluster.local:9091" not in raw:
                    errors.append(
                        "K8S configmaps.yaml [project policy]: prometheus-config scrape target must be "
                        "'pushgateway.monitoring.svc.cluster.local:9091'. "
                        "The pipeline pushes metrics to Pushgateway — Prometheus scrapes Pushgateway, not Trino."
                    )
                # Placeholder content means the LLM didn't embed the actual SQL/JSON.
                if any(phrase in raw.lower() for phrase in ["-- sql setup", "sql setup commands", "placeholder"]):
                    errors.append(
                        "K8S configmaps.yaml [project policy]: placeholder content detected — "
                        "embed the actual content of sql/setup_trino.sql and dashboards/monitoring_specs.json verbatim."
                    )
                # hive-catalog-config key must be hive.properties — not catalog.properties or hive.yaml.
                if "hive-catalog-config" in raw and "hive.properties" not in raw:
                    errors.append(
                        "K8S configmaps.yaml [project policy]: hive-catalog-config data key must be 'hive.properties' "
                        "(not 'catalog.properties' or any other name). Trino mounts /etc/trino/catalog/hive.properties."
                    )
                # Detect cloud from YAML content — never from an env var, which is
                # empty when the validator runs locally (outside the K8s runtime).
                if "hive.metastore=glue" in raw:
                    _cm_cloud = "aws"
                elif "hive.metastore=file" in raw and "abfss://" in raw:
                    _cm_cloud = "azure"
                elif "hive.metastore=file" in raw and "gs://" in raw:
                    _cm_cloud = "gcp"
                else:
                    _cm_cloud = os.getenv("CLOUD_PROVIDER", "").lower()
                if _cm_cloud == "aws" and "hive-catalog-config" in raw and "thrift://" in raw:
                    errors.append(
                        "K8S configmaps.yaml [project policy]: hive-catalog-config uses Thrift metastore "
                        "('hive.metastore.uri=thrift://...') on AWS — must use AWS Glue. "
                        "See k8s_deployment_rules.md Section 8.4 for the correct config: "
                        "hive.metastore=glue, hive.metastore.glue.region=<region>."
                    )
                # connector.name=hive is the first mandatory line — without it Trino ignores the file.
                if "hive-catalog-config" in raw and "hive.properties" in raw and "connector.name=hive" not in raw:
                    errors.append(
                        "K8S configmaps.yaml [project policy]: hive-catalog-config is missing 'connector.name=hive' — "
                        "this is the first required property in hive.properties. "
                        "Without it Trino does not recognize the file as a Hive connector config. "
                        "See k8s_deployment_rules.md Section 8.4 for the full required property set."
                    )
                # hive.metastore.glue.catalog.id is not in Section 8.4 and must not be added.
                # It is a cross-account Glue override. Same-account deployments do not need it,
                # and adding it (even with a real account ID) drifts from the verbatim standard.
                if "hive.metastore.glue.catalog.id" in raw:
                    errors.append(
                        "K8S configmaps.yaml [project policy]: hive.properties contains "
                        "'hive.metastore.glue.catalog.id' which is NOT in Section 8.4 of k8s_deployment_rules.md. "
                        "This is a cross-account Glue override — same-account deployments do not need it. "
                        "Remove it and copy Section 8.4 verbatim: connector.name=hive, hive.metastore=glue, "
                        "hive.metastore.glue.region, hive.s3.region, hive.s3.path-style-access, "
                        "hive.allow-drop-table, hive.allow-rename-table."
                    )

            elif fname in ("trino_deployment.yaml", "grafana_deployment.yaml", "prometheus_deployment.yaml"):
                # Every deployment file must contain at least one Service — without it the pods are unreachable.
                if "kind: Service" not in raw:
                    errors.append(
                        f"K8S {fname} [project policy]: missing Service resource — "
                        "a Deployment without a Service means the pod is unreachable by other pods. "
                        f"{'trino_deployment.yaml requires a ClusterIP Service named trino.' if 'trino' in fname else ''}"
                        f"{'grafana_deployment.yaml requires a LoadBalancer Service with aws-load-balancer-scheme annotation.' if 'grafana' in fname else ''}"
                        f"{'prometheus_deployment.yaml requires 4 objects: Prometheus Deployment+Service + Pushgateway Deployment+Service.' if 'prometheus' in fname else ''}"
                    )
                # Pushgateway must exist in prometheus_deployment.yaml.
                if fname == "prometheus_deployment.yaml" and "pushgateway" not in content_lower:
                    errors.append(
                        "K8S prometheus_deployment.yaml [project policy]: missing Pushgateway — "
                        "this file must contain 4 objects: Prometheus Deployment, Prometheus Service, "
                        "Pushgateway Deployment, Pushgateway Service. "
                        "Without Pushgateway the pipeline cannot push metrics."
                    )
                # Grafana LoadBalancer annotation required on AWS — without it EXTERNAL-IP is permanently pending.
                if fname == "grafana_deployment.yaml" and "aws-load-balancer-scheme" not in raw:
                    errors.append(
                        "K8S grafana_deployment.yaml [project policy]: missing aws-load-balancer-scheme annotation — "
                        "add 'service.beta.kubernetes.io/aws-load-balancer-scheme: internet-facing' to the Service "
                        "annotations. Without it the LoadBalancer defaults to internal and stays <pending>."
                    )

                # Per-deployment ConfigMap volume checks — kubectl cannot know which project ConfigMaps are required.
                if fname == "trino_deployment.yaml":
                    if "serviceaccountname" not in content_lower:
                        errors.append(
                            "K8S trino_deployment.yaml [project policy]: missing serviceAccountName — "
                            "Trino needs the IRSA service account to access S3 and Glue. "
                            "Set to the service account name from 00_namespaces.yaml."
                        )
                    if "hive-catalog-config" not in raw:
                        errors.append(
                            "K8S trino_deployment.yaml [project policy]: missing hive-catalog-config volumeMount — "
                            "mount hive-catalog-config at mountPath: /etc/trino/catalog (NOT /etc/trino). "
                            "trino-sql-config is a different ConfigMap — it mounts at /scripts for init-trino SQL scripts. "
                            "Two separate volumes are required: hive-catalog at /etc/trino/catalog, sql-scripts at /scripts. "
                            "See k8s_deployment_rules.md Section 3.1 skeleton."
                        )

                if fname == "grafana_deployment.yaml":
                    for _cm, _path in [
                        ("grafana-dash-config", "/etc/grafana/provisioning/dashboards"),
                        ("grafana-datasource-config", "/etc/grafana/provisioning/datasources"),
                    ]:
                        if _cm not in raw:
                            errors.append(
                                f"K8S grafana_deployment.yaml [project policy]: missing {_cm} volumeMount — "
                                f"must be mounted at {_path}. "
                                "Without it Grafana won't auto-provision the dashboard/datasource on startup."
                            )
                    # YAML-aware: volumeMounts must be inside containers[0], not at pod spec level.
                    # Service annotations must be in metadata, not ports[].
                    # Both are silent failures — Kubernetes accepts the YAML but ignores the misplaced fields.
                    try:
                        for _doc in yaml.safe_load_all(raw):
                            if not _doc:
                                continue
                            if _doc.get("kind") == "Deployment":
                                _pod_spec = (
                                    _doc.get("spec", {})
                                        .get("template", {})
                                        .get("spec", {})
                                )
                                if "volumeMounts" in _pod_spec:
                                    errors.append(
                                        "K8S grafana_deployment.yaml [project policy]: volumeMounts is at "
                                        "pod spec level (same indentation as 'containers:') — "
                                        "must be nested inside spec.template.spec.containers[0]. "
                                        "Kubernetes silently ignores pod-level volumeMounts; "
                                        "Grafana won't auto-provision dashboards or datasource. "
                                        "See k8s_deployment_rules.md Section 3.2 skeleton."
                                    )
                            elif _doc.get("kind") == "Service":
                                _svc_ports = _doc.get("spec", {}).get("ports", []) or []
                                if any("annotations" in _p for _p in _svc_ports):
                                    errors.append(
                                        "K8S grafana_deployment.yaml [project policy]: Service 'annotations' "
                                        "is inside spec.ports[] — must be at Service metadata.annotations. "
                                        "A port entry does not accept annotations; "
                                        "aws-load-balancer-scheme is silently ignored and the LoadBalancer "
                                        "stays <pending>. See k8s_deployment_rules.md Section 3.2."
                                    )
                    except yaml.YAMLError:
                        pass  # YAML syntax errors already caught by kubectl dry-run above

                if fname == "prometheus_deployment.yaml":
                    if "prometheus-config" not in raw:
                        errors.append(
                            "K8S prometheus_deployment.yaml [project policy]: missing prometheus-config volumeMount — "
                            "must be mounted at /etc/prometheus. "
                            "Without it Prometheus uses default config and won't scrape Pushgateway."
                        )
                    if "--config.file" not in raw:
                        errors.append(
                            "K8S prometheus_deployment.yaml [project policy]: missing "
                            "'--config.file=/etc/prometheus/prometheus.yml' arg — "
                            "Prometheus ignores the mounted config without this flag."
                        )
                    # Pushgateway must be a separate Deployment, not a sidecar inside the Prometheus pod.
                    # Detect: a pushgateway *image* appears in the containers list of the first YAML doc
                    # (the Prometheus Deployment). String-matching the first doc is insufficient because
                    # volume/volumeMount names like "pushgateway-config" trigger false positives.
                    try:
                        _first_parsed = yaml.safe_load(raw.split("---")[0])
                    except yaml.YAMLError:
                        _first_parsed = None
                    if _first_parsed:
                        _prom_containers = (
                            _first_parsed.get("spec", {})
                            .get("template", {})
                            .get("spec", {})
                            .get("containers", []) or []
                        )
                        if len(_prom_containers) > 1:
                            errors.append(
                                f"K8S prometheus_deployment.yaml [project policy]: Prometheus Deployment has "
                                f"{len(_prom_containers)} containers — must have EXACTLY 1 (prometheus). "
                                "Pushgateway is a separate Deployment, not a sidecar in the Prometheus pod. "
                                "Remove every container entry other than 'prometheus' from the Prometheus Deployment."
                            )
                        elif any("pushgateway" in (c.get("image", "") or "").lower()
                                 for c in _prom_containers):
                            errors.append(
                                "K8S prometheus_deployment.yaml [project policy]: Pushgateway is a sidecar "
                                "inside the Prometheus Deployment — it must be a separate Deployment. "
                                "The file must contain 4 objects: Prometheus Deployment + ClusterIP Service "
                                "+ Pushgateway Deployment + Pushgateway ClusterIP Service (separated by ---)."
                            )

    else:
        return f"CLEAN: '{filename}' — no validator for this file type."

    if errors:
        msg = "VALIDATION FAILED — fix before proceeding:\n\n" + "\n\n".join(errors)
        if warnings:
            msg += "\n\nNON-BLOCKING NOTES:\n" + "\n".join(f"  • {w}" for w in warnings)
        return msg
    msg = f"CLEAN: '{filename}' passed all validation checks."
    if warnings:
        msg += "\n  NOTE: " + " | ".join(warnings)
    return msg


@tool
def write_project_file(filename: str, content: str):
    """
    Writes project files. 
    If filename includes a directory path (e.g., 'custom/path/file.txt'), it uses that.
    Otherwise, it routes by extension: .py -> scripts/, .sql -> sql/, .json -> dashboards/, .csv -> data/.
    """
    # Check if the Agent provided a path (contains "/" or "\")
    has_custom_path = os.path.dirname(filename) != ""

    if has_custom_path:
        # If the Agent provided a path, use it as is
        filepath = filename
        final_dir = os.path.dirname(filepath)
    else:
        # If the Agent provided only a name, the "path router" is activated
        base_name = os.path.basename(filename)
        extension = os.path.splitext(base_name)[1].lower()

        if base_name.lower() == "requirements.txt":
            target_dir = "."
        else:
            folder_map = {
                ".py": "scripts",
                ".sql": "sql",
                ".json": "dashboards",
                ".csv": "data",
                ".md": "."
            }
            target_dir = folder_map.get(extension, "scripts")
        
        filepath = os.path.join(target_dir, base_name)
        final_dir = target_dir

    # Create the directory and write the file
    try:
        if final_dir and final_dir != ".":
            os.makedirs(final_dir, exist_ok=True)
            
        with open(filepath, 'w', encoding="utf-8") as f:
            f.write(content)
        return f"File saved successfully to {filepath}"
    except Exception as e:
        return f"Error writing file: {str(e)}"

@tool
def patch_project_file(filename: str, replacements: list) -> str:
    """
    Applies targeted find-and-replace edits to an existing file WITHOUT rewriting it.
    Use this in fix mode to apply surgical changes (e.g. os.getenv → cloud_get)
    while preserving all other code exactly as-is.

    Each item in `replacements` must be a dict with:
      - "old": the exact string to find (must be unique in the file)
      - "new": the string to replace it with

    Also handles adding a missing import line at the top of the file:
      {"old": "__ADD_IMPORT__", "new": "from utils.cloud_config import cloud_get"}

    Returns: summary of applied/skipped replacements, or an error message.
    """
    # Resolve path the same way write_project_file does
    has_custom_path = os.path.dirname(filename) != ""
    if has_custom_path:
        filepath = filename
    else:
        base_name = os.path.basename(filename)
        _ext = os.path.splitext(base_name)[1].lower()
        folder_map = {".py": "scripts", ".sql": "sql", ".json": "dashboards", ".csv": "data"}
        target_dir = folder_map.get(_ext, "scripts")
        filepath = os.path.join(target_dir, base_name)

    ext = os.path.splitext(filepath)[1].lower()  # always available after path resolution

    if not os.path.exists(filepath):
        return f"Error: '{filepath}' does not exist. Use write_project_file to create it first."

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        return f"Error reading '{filepath}': {e}"

    if not replacements:
        return (
            f"Error: replacements list is empty — patch_project_file requires at least one replacement. "
            f"To check a file without modifying it, use validate_generated_code instead."
        )

    applied, skipped = [], []

    for rep in replacements:
        old = rep.get("old", "")
        new = rep.get("new", "")

        # Special directive: add import line after the last existing import
        if old == "__ADD_IMPORT__":
            if ext not in (".py",):
                skipped.append(f"__ADD_IMPORT__ skipped — only valid for .py files, not '{ext}'")
                continue
            if new in content:
                skipped.append(f"import already present: {new}")
                continue
            # Insert after the last 'import' line
            lines = content.splitlines(keepends=True)
            last_import_idx = -1
            for i, line in enumerate(lines):
                if line.startswith("import ") or line.startswith("from "):
                    last_import_idx = i
            if last_import_idx >= 0:
                lines.insert(last_import_idx + 1, new + "\n")
                content = "".join(lines)
                applied.append(f"added import: {new}")
            else:
                content = new + "\n" + content
                applied.append(f"prepended import: {new}")
            continue

        if old not in content:
            skipped.append(f"not found: {repr(old[:60])}")
            continue

        count = content.count(old)
        content = content.replace(old, new, 1)
        applied.append(f"replaced ({count}x): {repr(old[:60])}")

    try:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception as e:
        return f"Error writing '{filepath}': {e}"

    lines_applied  = "\n  ".join(applied)  if applied  else "(none)"
    lines_skipped  = "\n  ".join(skipped)  if skipped  else "(none)"
    return (
        f"PATCH APPLIED to '{filepath}'.\n"
        f"Applied:\n  {lines_applied}\n"
        f"Skipped:\n  {lines_skipped}"
    )


@tool
def read_data_schema(table_name: str, db_type: str = "postgres"):
    """
    Connects to the database and returns the table schema and a sample of rows.
    Supports: postgres (AWS RDS), mysql, sqlite.
    """

    try:
        # 1. Build the connection URL dynamically based on db_type + cloud provider.
        # Cloud is read from CLOUD_PROVIDER env var (set by the pipeline) so this
        # tool works for any cloud+DB combination without hardcoded assumptions.
        cloud = os.getenv("CLOUD_PROVIDER", "aws").lower()

        if db_type == "postgres":
            host = cloud_get(cloud, "db_host",     db_type="postgres")
            port = cloud_get(cloud, "db_port",     db_type="postgres") or "5432"
            user = cloud_get(cloud, "db_user",     db_type="postgres")
            pw   = cloud_get(cloud, "db_password", db_type="postgres")
            db   = cloud_get(cloud, "db_name",     db_type="postgres")
            db_url = f"postgresql://{user}:{pw}@{host}:{port}/{db}"

        elif db_type == "mysql":
            host = cloud_get(cloud, "db_host",     db_type="mysql")
            port = cloud_get(cloud, "db_port",     db_type="mysql") or "3306"
            user = cloud_get(cloud, "db_user",     db_type="mysql")
            pw   = cloud_get(cloud, "db_password", db_type="mysql")
            db   = cloud_get(cloud, "db_name",     db_type="mysql")
            db_url = f"mysql+pymysql://{user}:{pw}@{host}:{port}/{db}"

        elif db_type == "sqlite":
            # For SQLite, read the URL/path directly from .env
            db_url = os.getenv("SQLITE_SALES_URL")

        else:
            return f"Error: Unsupported db_type '{db_type}'"

        if not db_url:
            return f"Error: Connection details for {db_type} not found in SSM, .bootstrap_outputs.json, or env vars"

        # 2. Create engine and fetch metadata
        engine = create_engine(db_url)
        inspector = inspect(engine)
        
        # Fetch columns
        columns = inspector.get_columns(table_name)
        if not columns:
            return f"Table '{table_name}' not found in {db_type}."
            
        schema_desc = [f"{col['name']} ({col['type']})" for col in columns]
        
        # 3. Fetch sample data
        with engine.connect() as conn:
            # LIMIT 3 is enough for the agent to infer structure
            result = conn.execute(text(f"SELECT * FROM {table_name} LIMIT 3"))
            sample = [dict(row._mapping) for row in result.fetchall()]
        
        return {
            "status": "success",
            "database": db_type,
            "table": table_name,
            "schema": schema_desc,
            "sample_data": sample
        }

    except Exception as e:
        return f"Database Error on {db_type}: {str(e)}"


# --- TERRAFORM TOOLS ---

# Canonical Terraform filenames for this repo (Infra agent must not invent pipeline-*.tf names).
_CANONICAL_TF_FILES = frozenset({"providers.tf", "main.tf", "variables.tf", "outputs.tf"})


@tool
def write_terraform_config(filename: str, content: str):
    """
    Saves Terraform HCL to the shared terraform/ directory.
    Validation is handled during the terraform execution phase.
    """
    target_path = Path("terraform") / os.path.basename(filename)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    # Sanitize escape sequences the LLM emits as literal characters.
    # \\n → real newline, \\" → real quote — both are invalid HCL syntax.
    sanitized = content.replace("\\n", "\n").replace('\\"', '"')

    try:
        with open(target_path, "w", encoding="utf-8") as f:
            f.write(sanitized)
        return f"File {filename} successfully written to {target_path}."
    except Exception as e:
        return f"File Write Error: {str(e)}"

@tool
def execute_terraform(command: str, vars_dict: dict = None):
    """
    Executes terraform commands against the shared terraform/ directory.
    Supports init, plan, apply, and destroy with automated CI/CD flags.
    """
    terraform_dir = Path("terraform")

    if not terraform_dir.exists():
        return "Error: terraform/ directory not found. Run write_terraform_config first."

    # 2. CI/CD OPTIMIZATION FLAGS
    # A professional knows that Agents/CI cannot press "yes"
    auto_flags = {
        "init": ["-reconfigure", "-input=false"],
        "apply": ["-auto-approve", "-input=false"],
        "destroy": ["-auto-approve", "-input=false"],
        "plan": ["-input=false"]
    }

    try:
        parts = shlex.split(command.strip())
        subcommand = parts[0].lower()
    except (ValueError, IndexError):
        return "Error: Invalid or empty Terraform command."

    # Build the base command
    cmd = ["terraform", subcommand]
    
    # Add standardized flags if not already present
    for flag in auto_flags.get(subcommand, []):
        if flag not in parts:
            cmd.append(flag)
            
    # Add any other arguments the LLM provided (minus the subcommand)
    cmd.extend(parts[1:])

    # 3. PROFESSIONAL VARIABLE HANDLING
    # Instead of searching for files, pass the vars via -var flags
    if vars_dict and isinstance(vars_dict, dict):
        for key, value in vars_dict.items():
            cmd.extend(["-var", f"{key}={value}"])

    # 4. EXECUTION WITH ERROR CAPTURE
    try:
        # Use Popen or run with clear separation of stdout/stderr
        result = subprocess.run(
            cmd,
            cwd=str(terraform_dir),
            capture_output=True,
            text=True,
            timeout=600 # 10 minutes for infra tasks
        )

        if result.returncode == 0:
            return f"SUCCESS: Terraform {subcommand}\n{result.stdout}"
        else:
            # Return the stderr to the Medic for diagnosis
            return f"FAILED: Terraform {subcommand}\nERROR: {result.stderr}\nOUTPUT: {result.stdout}"

    except Exception as e:
        return f"CRITICAL SYSTEM ERROR: {str(e)}"

# --- DOCKER & K8S TOOLS ---

@tool
def generate_dockerfile(content: str):
    """
    Generates a Dockerfile.
    Must include 'pandas', 'sqlalchemy', 'psycopg2-binary' and 'pymysql'.
    Use python:3.11-slim for OCI/Kubernetes optimization.
    MANDATORY COPY statements (in this order):
      COPY requirements.txt .
      RUN pip install ...
      COPY utils/ utils/
      COPY scripts/ scripts/
    COPY utils/ utils/ is required because pipeline scripts import from utils.cloud_config.
    """
    try:
        with open("Dockerfile", "w", encoding="utf-8") as f:
            f.write(content)
        # Return the full path so infra_node can consume it.
        full_path = os.path.abspath("Dockerfile")
        return f"Dockerfile generated successfully. File saved to {full_path}"
    except Exception as e:
        return f"Failed to generate Dockerfile: {str(e)}"

@tool
def generate_docker_compose(content: str):
    """
    Generates a docker-compose.yml file. 
    Use this to orchestrate the environment (databases + runner) for local testing.
    """
    with open("docker-compose.yml", "w", encoding="utf-8") as f:
        f.write(content)
    return "docker-compose.yml generated successfully."


@tool
def execute_docker_command(image_name: str, registry_url: str = None, tag: str = "latest"):
    """
    Builds a Docker image and pushes it if a registry is provided.
    Returns a standardized STATUS prefix for state tracking.
    """
    try:
        # 1. Determine the full image path
        is_remote = registry_url or ("." in image_name and "/" in image_name)
        full_image_path = f"{registry_url}:{tag}" if registry_url else f"{image_name}:{tag}"
        
        # 2. Docker Build
        logger.info(f"Starting build for: {full_image_path}")
        build_res = subprocess.run(["docker", "build", "-t", full_image_path, "."], capture_output=True, text=True)
        
        if build_res.returncode != 0:
            return f"STATUS: ERROR | Message: Docker Build Failed: {build_res.stderr}"
            
        # 3. Docker Push
        if is_remote:
            logger.info(f"Pushing image to registry: {full_image_path}")
            push_res = subprocess.run(["docker", "push", full_image_path], capture_output=True, text=True)
            
            if push_res.returncode != 0:
                return f"STATUS: ERROR | Message: Docker Push Failed: {push_res.stderr}"
            
            return f"STATUS: SUCCESS | Message: Image successfully built and pushed to {full_image_path}"
            
        return f"STATUS: SUCCESS | Message: Image {image_name} built successfully locally."
        
    except Exception as e:
        return f"STATUS: ERROR | Message: System error during Docker execution: {str(e)}"

@tool
def generate_k8s_manifest(filename: str, content: str):
    """
    Generates K8s manifests. Automatically creates 'k8s' directory.

    MANDATORY PINNED IMAGE VERSIONS — never use ':latest':
      trinodb/trino:403
      grafana/grafana:10.4.2
      prom/prometheus:v2.51.0
      prom/pushgateway:v1.8.0
      For ECR custom images: use the actual ECR repository URL from execute_terraform output
        (e.g. 123456789012.dkr.ecr.eu-central-1.amazonaws.com/eu-sales-pipeline-repo:latest)
        — extract the 12-digit account ID from the ECR URL, never write <AWS_ACCOUNT_ID>.

    MANDATORY job.yaml fields:
      spec.backoffLimit: 0
      spec.template.spec.containers[].envFrom: [{secretRef: {name: <project_id>-db-credentials}}]
      env vars required: PROJECT_ID, CLOUD_PROVIDER, TRINO_HOST, PUSHGATEWAY_URL

    MANDATORY configmaps.yaml: all 5 ConfigMaps in one file (separated by ---):
      trino-sql-config (namespace: analytics)
      hive-catalog-config (namespace: analytics)
      grafana-dash-config (namespace: monitoring)
      grafana-datasource-config (namespace: monitoring)
      prometheus-config (namespace: monitoring, scrape target: pushgateway.monitoring.svc.cluster.local:9091)
    """
    target_dir = "k8s"
    os.makedirs(target_dir, exist_ok=True)
    
    # Remove any existing .yaml/.yml to re-append it cleanly, 
    # but also check if the agent is trying to pass an SQL file as a manifest
    # Strip k8s/ prefix if the LLM includes it — the tool always writes into k8s/
    basename = os.path.basename(filename)
    clean_name = basename.replace(".yaml", "").replace(".yml", "")

    if clean_name.endswith(".sql"):
        clean_name = clean_name.replace(".sql", "_config")

    filepath = os.path.join(target_dir, f"{clean_name}.yaml")
    
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)
    return f"K8s manifest saved to {filepath}"

@tool
def execute_kubectl_apply(filename: str):
    """
    Executes 'kubectl apply -f' on a generated manifest file.
    The file must exist in the 'k8s/' directory.
    """
    filepath = os.path.join("k8s", os.path.basename(filename))
    if not os.path.exists(filepath):
        return f"Error: Manifest {filepath} not found."

    try:
        result = subprocess.run(["kubectl", "apply", "-f", filepath], capture_output=True, text=True)
        if result.returncode == 0:
            return f"K8s deployment successful:\n{result.stdout}"
        else:
            return f"K8s deployment failed:\n{result.stderr}"
    except Exception as e:
        return f"System error during kubectl execution: {str(e)}"

# --- GITHUB & CI/CD TOOLS ---

_GITHUB_API = "https://api.github.com"
# Cap total log text returned to agents (LLM context).
_GITHUB_LOGS_MAX_CHARS = int(os.getenv("GITHUB_FETCH_LOGS_MAX_CHARS", "200000"))


def _github_token() -> str | None:
    return (os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN") or "").strip() or None


def _github_repository_explicit(repository: str) -> tuple[str, str] | None:
    repository = (repository or "").strip()
    if not repository or "/" not in repository:
        return None
    owner, _, repo = repository.partition("/")
    owner, repo = owner.strip(), repo.strip().strip("/")
    if not owner or not repo:
        return None
    return owner, repo


def _github_repository_from_env() -> tuple[str, str] | None:
    # GitHub Actions sets GITHUB_REPOSITORY=owner/repo
    explicit = os.getenv("GITHUB_REPOSITORY", "").strip()
    if explicit and "/" in explicit:
        return _github_repository_explicit(explicit)
    return None


def _github_request(
    method: str,
    url: str,
    token: str,
    *,
    accept: str = "application/vnd.github+json",
) -> tuple[int, bytes, str]:
    req = urllib.request.Request(url, method=method.upper())
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", accept)
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            ct = (resp.headers.get("Content-Type") or "") if resp.headers else ""
            return resp.getcode() or 200, resp.read(), ct
    except urllib.error.HTTPError as e:
        body = e.read() if e.fp else b""
        ct = ""
        if e.headers:
            ct = e.headers.get("Content-Type") or ""
        return e.code, body, ct


def _github_get_json(url: str, token: str):
    status, body, _ct = _github_request("GET", url, token)
    text = body.decode("utf-8", errors="replace")
    if status >= 400:
        raise RuntimeError(f"GitHub API HTTP {status}: {text[:2000]}")
    return json.loads(text) if text.strip() else {}


def _github_resolve_workflow_run_id(
    token: str, owner: str, repo: str, run_id: str, project_id: str = ""
) -> tuple[str | None, str | None]:
    """
    GitHub workflow run IDs are numeric. Accept digits, or empty / 'latest' / 'last' to use newest run.
    When project_id is provided, scopes the lookup to the project's own workflow file
    ({project_id}_pipeline.yml) to avoid picking up unrelated runs in a monorepo.
    Returns (resolved_id, error_message).
    """
    raw = str(run_id).strip()
    if raw.isdigit():
        return raw, None
    low = raw.lower()
    if not raw or low in ("latest", "last", "recent"):
        if project_id:
            workflow_file = f"{project_id}_pipeline.yml"
            list_url = f"{_GITHUB_API}/repos/{owner}/{repo}/actions/workflows/{workflow_file}/runs?per_page=1"
        else:
            list_url = f"{_GITHUB_API}/repos/{owner}/{repo}/actions/runs?per_page=1"
        try:
            data = _github_get_json(list_url, token)
        except Exception as e:
            return None, f"Could not list workflow runs: {e}"
        runs = data.get("workflow_runs") or []
        if not runs:
            return None, "No workflow runs found for this repository."
        return str(runs[0]["id"]), None
    return None, (
        f"Invalid run_id {raw!r}. GitHub Actions run IDs are numeric only (see the workflow URL: "
        f".../actions/runs/<RUN_ID>). "
        f"Values such as pipeline or project labels are not valid. "
        f"Use run_id='latest' or '' to fetch the most recent run's logs."
    )


def _github_decode_log_body(body: bytes, content_type: str) -> str:
    """
    Job logs may be plain text or a ZIP (after redirect). Concatenate text from ZIP members.
    """
    ct = (content_type or "").lower()
    if "zip" in ct or (len(body) > 2 and body[:2] == b"PK"):
        try:
            with zipfile.ZipFile(io.BytesIO(body)) as zf:
                pieces: list[str] = []
                for name in sorted(zf.namelist()):
                    if name.endswith("/"):
                        continue
                    raw = zf.read(name)
                    pieces.append(raw.decode("utf-8", errors="replace"))
                return "\n\n---\n\n".join(pieces) if pieces else "[empty zip]"
        except zipfile.BadZipFile:
            pass
    return body.decode("utf-8", errors="replace")


class _StripAuthOnRedirect(urllib.request.HTTPRedirectHandler):
    """Follow GitHub → Azure Blob redirects without leaking the Bearer token.
    GitHub returns a 302 to an Azure SAS URL; the SAS token in the URL is the auth —
    sending the GitHub Bearer token to Azure causes InvalidAuthenticationInfo.
    """
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new_req = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new_req is not None and "github.com" not in new_req.get_full_url():
            new_req.headers.pop("Authorization", None)
            new_req.unredirected_hdrs.pop("Authorization", None)
        return new_req


def _github_get_job_log_text(url: str, token: str) -> str:
    """GET .../actions/jobs/{id}/logs — follows GitHub's redirect to Azure Blob Storage.
    Uses _StripAuthOnRedirect to avoid sending the Bearer token to Azure.
    """
    opener = urllib.request.build_opener(_StripAuthOnRedirect)
    req = urllib.request.Request(url, method="GET")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")

    try:
        with opener.open(req, timeout=120) as resp:
            status = resp.getcode() or 200
            body = resp.read()
            ct = (resp.headers.get("Content-Type") or "") if resp.headers else ""
    except urllib.error.HTTPError as e:
        body = e.read() if e.fp else b""
        ct = (e.headers.get("Content-Type") or "") if e.headers else ""
        status = e.code

    if status == 204:
        return ""
    if status == 404:
        return f"[no log body: HTTP {status}]"
    if status >= 400:
        return body.decode("utf-8", errors="replace")[:8000]
    return _github_decode_log_body(body, ct)


@tool(return_direct=True)
def generate_github_action(project_id: str, content: str):
    """
    Generates a GitHub Actions workflow file in the REPOSITORY ROOT (.github/workflows/).
    The filename is always {project_id}_pipeline.yml — do NOT pass a custom name.
    Sanitizes line breaks to ensure valid YAML syntax.

    STANDALONE REPO — never use 'projects/...' path prefixes. All paths are relative
    to the repository root:
      • docker build context: '.'  (not 'projects/multi-cloud-self-healing-agent/')
      • Dockerfile path: 'Dockerfile'  (not 'projects/.../Dockerfile')
      • kubectl apply: 'k8s/job.yaml'  (not 'projects/.../k8s/job.yaml')
      • on.push.paths: omit entirely or use '**'

    AWS_ACCOUNT_ID: extract the 12-digit prefix from the ECR repository URL returned
    by execute_terraform (e.g. '123456789012.dkr.ecr.eu-central-1.amazonaws.com/...')
    — never write <AWS_ACCOUNT_ID> as a literal placeholder.
    """
    workflow_dir = os.path.join(REPO_ROOT, ".github", "workflows")
    os.makedirs(workflow_dir, exist_ok=True)

    sanitized_content = content.replace("\\n", "\n")
    # LLMs occasionally strip the '$' from GitHub Actions expressions (e.g. '{{ github.sha }}'
    # instead of '${{ github.sha }}'). Fix any bare '{{' not preceded by '$'.
    sanitized_content = re.sub(r"(?<!\$)\{\{", "${{", sanitized_content)

    workflow_name = f"{project_id}_pipeline.yml"
    filepath = os.path.join(workflow_dir, workflow_name)

    try:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(sanitized_content)

        return (f"SUCCESS: GitHub Action workflow generated successfully. "
                f"File saved to {filepath}. Line breaks sanitized. "
                f"The task is COMPLETE.")

    except Exception as e:
        return f"Error writing workflow to root: {str(e)}"

@tool
def push_to_github(project_id: str, commit_message: str):
    """
    Stages changes for a specific project and pushes them to the repository.
    Identity is automated as github-actions[bot].
    """
    try:
        # 1. Identity Config
        subprocess.run(["git", "config", "user.name", "github-actions[bot]"], cwd=REPO_ROOT, check=True)
        subprocess.run(["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"], cwd=REPO_ROOT, check=True)

        # 2. Authentication: in CI (GitHub Actions) the default remote URL is HTTPS
        # without a token embedded, causing 'git push' to fail with exit 128.
        # Rewrite the remote URL to use GITHUB_TOKEN so the push is authenticated.
        # Locally, GITHUB_TOKEN / GITHUB_REPOSITORY are not set → this block is skipped
        # and git uses whatever credentials are configured on the developer's machine.
        github_token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
        github_repository = os.getenv("GITHUB_REPOSITORY")
        if github_token and github_repository:
            authed_url = f"https://x-access-token:{github_token}@github.com/{github_repository}.git"
            subprocess.run(
                ["git", "remote", "set-url", "origin", authed_url],
                cwd=REPO_ROOT, check=True
            )
            logger.info("🔑 CI detected: remote URL configured with GITHUB_TOKEN.")

        # 3. Selective Staging — stage every directory/file that agents generate.
        # REPO_ROOT == PROJECT_ROOT (standalone repo — not a monorepo anymore).
        # All generated artifacts land directly under REPO_ROOT, so paths are simple.
        paths_to_add = [
            "scripts",
            "sql",
            "terraform",
            "dashboards",
            "data",
            "k8s",
            "Dockerfile",
            "requirements.txt",
            ".github/workflows/",
        ]
        for path in paths_to_add:
            if os.path.exists(os.path.join(REPO_ROOT, path)):
                subprocess.run(["git", "add", path], cwd=REPO_ROOT, check=True)

        # 4. Commit if there are staged changes
        full_message = f"fix({project_id}): {commit_message}"

        status = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=REPO_ROOT)
        has_staged = status.returncode != 0

        if has_staged:
            subprocess.run(["git", "commit", "-m", full_message], cwd=REPO_ROOT, check=True)

        # 5. Check for unpushed commits — handles the case where a previous call
        # committed successfully but the push failed (exit 128). git status -sb
        # reports "ahead N" when local commits exist that aren't on the remote,
        # and omits "..." entirely when no upstream is set (first push).
        branch_status_result = subprocess.run(
            ["git", "status", "-sb"], cwd=REPO_ROOT, capture_output=True, text=True
        )
        branch_line = (branch_status_result.stdout.splitlines() or [""])[0]
        needs_push = "ahead" in branch_line or "..." not in branch_line

        if not has_staged and not needs_push:
            return f"STATUS: SUCCESS | Message: No changes detected for project {project_id}."

        # -u origin HEAD: sets upstream tracking on first push and works for any branch name.
        subprocess.run(["git", "push", "-u", "origin", "HEAD"], cwd=REPO_ROOT, check=True)

        sha_result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
        )
        commit_sha = sha_result.stdout.strip()

        return f"STATUS: SUCCESS | SHA: {commit_sha} | Message: Successfully pushed changes for {project_id} to GitHub."
        
    except Exception as e:
        return f"STATUS: ERROR | Message: Git Push Error: {str(e)}"



@tool
def query_vector_store(query: str):
    """
    Searches the Unified Intelligence Fabric across Static Specs and Dynamic Experience.
    Returns prioritized architectural guidelines and past successful fixes.
    """
    if index is None or embeddings_model is None:
        return "Pinecone not initialized. Check PINECONE_API_KEY and PINECONE_INDEX_NAME."
    try:
        query_vector = embeddings_model.embed_query(query)

        all_results = []
        namespaces = ["engineering-standards", "dynamic-experience"]

        for ns in namespaces:
            res = index.query(
                vector=query_vector,
                top_k=3,
                include_metadata=True,
                namespace=ns
            )
            
            for match in res['matches']:
                score = match['score']
                if score < 0.5:
                    continue

                content = match['metadata'].get('content', 'No content')
                source = match['metadata'].get('source', 'Unknown')

                # Semantic labeling for the Agents
                if ns == "engineering-standards":
                    prefix = "🛡️ [OFFICIAL SPEC]"
                else:
                    prefix = "💡 [PAST EXPERIENCE]"

                all_results.append({
                    "score": score,
                    "text": f"{prefix}\nSource: {source} (Relevance: {score:.2f})\nContent: {content}"
                })

        # Sort by relevance across both namespaces
        all_results.sort(key=lambda x: x['score'], reverse=True)
        
        if not all_results:
            return "No relevant guidelines found. Proceed with standard engineering practices."

        return "\n\n---\n\n".join([r['text'] for r in all_results[:4]]) # Return top 4 combined

    except Exception as e:
        logger.error(f"Vector Store Error: {e}")
        return f"Error querying Intelligence Fabric: {str(e)}"

def _normalize_handoff_agent(agent_name: str) -> str:
    """Map free-form LLM labels to supervisor routing keys."""
    raw = (agent_name or "").strip().lower()
    if raw in {"infra", "infrastructure", "terraform", "devops", "docker", "k8s", "kubernetes", "ci"}:
        return "infra"
    if raw in {"architect", "architecture", "arch"}:
        return "architect"
    if any(x in raw for x in ("infra", "terraform", "docker", "k8s", "kube")):
        return "infra"
    if "arch" in raw:
        return "architect"
    return "architect"


@tool
def request_fix(target_agent: str, issue_description: str, suggested_fix: str):
    """
    Sends a formal technical fix request to the Supervisor.
    - target_agent: 'architect' or 'infra'
    - issue_description: Detailed summary of the error found in logs.
    - suggested_fix: Exact technical steps or code snippets to resolve the issue.
    """
    payload = {
        "status": "REJECTED_BY_MEDIC",
        "target_agent": _normalize_handoff_agent(target_agent),
        "diagnosis": issue_description,
        "healing_instructions": suggested_fix,
    }
    return json.dumps(payload, ensure_ascii=False)

@tool
def fetch_github_action_logs(project_id: str, head_sha: str = "", run_id: str = "latest", repository: str = ""):
    """
    Downloads logs for the GitHub Actions run triggered by a specific commit (head_sha).
    Falls back to the latest run when head_sha is not provided.
    Scoped by project_id to maintain monorepo isolation.
    """
    token = _github_token()
    if not token:
        return "Error: GITHUB_TOKEN not found."

    owner_repo = _github_repository_explicit(repository) or _github_repository_from_env()
    if not owner_repo:
        return "Error: Could not resolve repository."

    owner, repo = owner_repo

    if head_sha:
        workflow_file = f"{project_id}_pipeline.yml"
        list_url = (
            f"{_GITHUB_API}/repos/{owner}/{repo}/actions/workflows"
            f"/{workflow_file}/runs?head_sha={head_sha}&per_page=1"
        )
        try:
            data = _github_get_json(list_url, token)
        except Exception as e:
            err_str = str(e)
            if "403" in err_str or "forbidden" in err_str.lower():
                return (
                    f"PENDING: PERMISSIONS_ERROR — GitHub API returned 403 for {workflow_file}. "
                    f"GH_TOKEN lacks 'actions: read' scope. This is a token configuration issue, "
                    f"not a code bug. Grant 'actions: read' permission to GH_TOKEN and retry."
                )
            return f"Error resolving run for SHA {head_sha}: {e}"
        runs = data.get("workflow_runs", [])
        if not runs:
            return (
                f"PENDING: No run found yet for SHA {head_sha} in {workflow_file}. "
                f"GitHub may still be queuing the workflow. Retry later."
            )
        resolved_id = str(runs[0]["id"])
    else:
        resolved_id, err = _github_resolve_workflow_run_id(token, owner, repo, run_id, project_id=project_id)
        if err:
            if "no workflow runs found" in err.lower():
                return (
                    f"PENDING: No runs found yet for {project_id}_pipeline.yml. "
                    f"Workflow may still be queued after the recent push. Retry later."
                )
            if "403" in err or "forbidden" in err.lower():
                return (
                    f"PENDING: PERMISSIONS_ERROR — GitHub API returned 403 for {project_id}_pipeline.yml. "
                    f"GH_TOKEN lacks 'actions: read' scope. This is a token configuration issue, "
                    f"not a code bug. Grant 'actions: read' permission to GH_TOKEN and retry."
                )
            return f"Error: {err}"

    # 1. Check run-level status first — this is the authoritative signal.
    # Job-level statuses can lag behind (race condition in GitHub API), but the run
    # conclusion is only set once the entire run is truly done.
    run_url = f"{_GITHUB_API}/repos/{owner}/{repo}/actions/runs/{resolved_id}"
    try:
        run_data = _github_get_json(run_url, token)
    except Exception as e:
        return f"Error fetching run metadata: {e}"

    run_status = run_data.get("status")       # "queued", "in_progress", "completed"
    run_conclusion = run_data.get("conclusion")  # "success", "failure", "cancelled", "timed_out", None

    if run_status != "completed":
        return f"PENDING: Run {resolved_id} still in progress (status: {run_status}). Retry later."

    if run_conclusion == "success":
        return f"No failed jobs found in run {resolved_id}. Everything looks green!"

    # 2. Run is completed (and not success) — fetch failed jobs for diagnosis
    jobs_url = f"{_GITHUB_API}/repos/{owner}/{repo}/actions/runs/{resolved_id}/jobs"
    try:
        data = _github_get_json(jobs_url, token)
        jobs = data.get("jobs", [])
    except Exception as e:
        return f"Error fetching jobs: {e}"

    failed_jobs = [j for j in jobs if j.get("conclusion") in ["failure", "timed_out"]]

    if not failed_jobs:
        return f"Run {resolved_id} completed with conclusion '{run_conclusion}' but no individual job failures found."

    parts = [f"--- DEBUGGING LOGS FOR PROJECT: {project_id} ---"]
    
    for j in failed_jobs:
        jid = j["id"]
        name = j["name"]
        log_url = f"{_GITHUB_API}/repos/{owner}/{repo}/actions/jobs/{jid}/logs"
        
        try:
            raw_log = _github_get_job_log_text(log_url, token)
            
            # 3. TAIL LOGS: Keep the last 100 lines
            # That's where the error hides, not in the environment setup
            log_lines = raw_log.splitlines()
            tail_log = "\n".join(log_lines[-100:]) if len(log_lines) > 100 else raw_log
            
            parts.append(f"\n❌ JOB FAILED: {name}\nID: {jid}\n{'-'*20}\n{tail_log}")
            
        except Exception as e:
            parts.append(f"Could not fetch logs for {name}: {e}")

    return "\n".join(parts)

@tool
def store_architectural_insight(error_summary: str, solution: str, cloud_provider: str):
    """
    Stores a successful technical solution in the long-term memory (Pinecone).
    Use this ONLY when a fix is verified and should be remembered for the future.
    """
    import uuid

    # 1. Initialize Pinecone — use local variables (_pc, _idx) to avoid
    # shadowing the module-level 'index', which causes UnboundLocalError:
    # Python sees the assignment 'index = ...' and treats 'index' as local
    # throughout the entire function, even before the assignment line.
    if not os.getenv("PINECONE_API_KEY"):
        return "Pinecone not initialized. Check PINECONE_API_KEY and PINECONE_INDEX_NAME."
    _pc = Pinecone(api_key=os.getenv("PINECONE_API_KEY"))
    _index_name = os.getenv("PINECONE_INDEX_NAME", "unified-intelligence-fabric")
    _idx = _pc.Index(_index_name)
    
    # 2. Prepare the text for embedding
    insight_text = f"ISSUE: {error_summary}\nFIX: {solution}\nPROVIDER: {cloud_provider}"
    
    # 3. Generate Embedding (using the same logic as the injection script)
    vector = get_embedding(insight_text) # Use text-embedding-3-small
    
    if vector:
        # 4. Upsert to Pinecone in the 'dynamic-experience' namespace
        _idx.upsert(
            vectors=[(
                f"fix-{uuid.uuid4()}", 
                vector, 
                {
                    "category": "experience",
                    "provider": cloud_provider,
                    "content": insight_text, # Unified key
                    "type": "successful_fix",
                    "timestamp": time.time()
                }
            )],
            namespace="dynamic-experience"
        )
        return "✨ Insight successfully stored in the Intelligence Fabric (dynamic-experience)."
    
    return "❌ Failed to store insight due to embedding error."
