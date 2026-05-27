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
REPO_ROOT = PROJECT_ROOT.parent.parent

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
    Runs static analysis (ruff + py_compile) on a generated Python file.
    Call this immediately after write_project_file for every .py artifact.
    Returns 'CLEAN' if no issues found, otherwise returns the errors to fix.
    """
    if not filename.endswith(".py"):
        return "CLEAN: not a Python file, no validation needed."

    if not os.path.exists(filename):
        return f"Error: file '{filename}' does not exist. Did write_project_file succeed?"

    errors = []

    # 1. Syntax check — catches syntax errors and bad imports structurally
    import py_compile
    try:
        py_compile.compile(filename, doraise=True)
    except py_compile.PyCompileError as e:
        errors.append(f"SYNTAX ERROR:\n{e}")

    # 2. Ruff static analysis — catches undefined names, unused imports, style
    ruff_path = shutil.which("ruff")
    if ruff_path:
        result = subprocess.run(
            [ruff_path, "check", "--select", "F,E9", "--no-cache", filename],
            capture_output=True, text=True
        )
        if result.stdout.strip():
            errors.append(f"RUFF ERRORS:\n{result.stdout.strip()}")
    else:
        errors.append("WARNING: ruff not found — only syntax check was performed.")

    if errors:
        return "VALIDATION FAILED — fix these issues before proceeding:\n\n" + "\n\n".join(errors)
    return f"CLEAN: '{filename}' passed all static analysis checks."


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
def read_data_schema(table_name: str, db_type: str = "postgres"):
    """
    Connects to the database and returns the table schema and a sample of rows.
    Supports: postgres (AWS RDS), mysql, sqlite.
    """

    try:
        # 1. Build the connection URL dynamically based on db_type
        if db_type == "postgres":
            # Priority: SSM → .bootstrap_outputs.json → env vars
            host = cloud_get("aws", "rds_host")
            port = cloud_get("aws", "rds_port") or "5432"
            user = cloud_get("aws", "rds_username")
            pw   = cloud_get("aws", "rds_password")
            db   = cloud_get("aws", "rds_db_name")
            db_url = f"postgresql://{user}:{pw}@{host}:{port}/{db}"

        elif db_type == "mysql":
            # Priority: .bootstrap_outputs.json → env vars (GCP Cloud SQL)
            host = cloud_get("gcp", "db_host")
            port = cloud_get("gcp", "db_port") or "3306"
            user = cloud_get("gcp", "db_user")
            pw   = cloud_get("gcp", "db_password")
            db   = cloud_get("gcp", "db_name")
            # Use the pymysql driver for MySQL
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
    """Generates K8s manifests. Automatically creates 'k8s' directory."""
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

        # 2. Selective Staging — paths relative to REPO_ROOT (monorepo root).
        # PROJECT_ROOT is the self-healing project dir inside the monorepo.
        project_rel = os.path.relpath(PROJECT_ROOT, REPO_ROOT)
        paths_to_add = [
            os.path.join(project_rel, "projects", project_id),
            os.path.join(project_rel, "scripts"),
            os.path.join(project_rel, "sql"),
            os.path.join(project_rel, "dashboards"),
            os.path.join(project_rel, "data"),
            os.path.join(project_rel, "k8s"),
            os.path.join(project_rel, "Dockerfile"),
            os.path.join(project_rel, "requirements.txt"),
            ".github/workflows/",
        ]
        for path in paths_to_add:
            if os.path.exists(os.path.join(REPO_ROOT, path)):
                subprocess.run(["git", "add", path], cwd=REPO_ROOT, check=True)

        # 3. Commit with Scope
        full_message = f"fix({project_id}): {commit_message}"
        
        status = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=REPO_ROOT)
        if status.returncode == 0:
            return f"STATUS: SUCCESS | Message: No changes detected for project {project_id}."

        subprocess.run(["git", "commit", "-m", full_message], cwd=REPO_ROOT, check=True)
        subprocess.run(["git", "push"], cwd=REPO_ROOT, check=True)

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
