import os

# 1. Project Root Discovery (Absolute path to project base)
# This goes two levels up from agents/constants.py to reach the root
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 2. Prompt Files Names
ARCHITECT_PROMPT_FILE = "architect.md"
INFRA_PROMPT_FILE = "infra.md"
MEDIC_PROMPT_FILE = "medic.md"
SUPERVISOR_PROMPT_FILE = "supervisor.md"

# 3. Directory Paths
CONFIGS_DIR = os.path.join(BASE_DIR, "configs")
INFRA_CONFIGS_DIR = os.path.join(CONFIGS_DIR, "infra")
PROMPTS_DIR = os.path.join(BASE_DIR, "agents", "prompts")

# 4. Global Settings (Optional)
MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
TEMPERATURE = 0

DEFAULT_REQUIRED_ARTIFACTS = [
    "scripts/{pipeline_name}.py",
    "sql/setup_trino.sql",
    "dashboards/monitoring_specs.json",
    "requirements.txt",
]

# Fallback when infrastructure config omits required_k8s_manifests.
DEFAULT_REQUIRED_K8S_MANIFESTS = [
    "k8s/00_namespaces.yaml",
    "k8s/trino_deployment.yaml",
    "k8s/grafana_deployment.yaml",
    "k8s/prometheus_deployment.yaml",
    "k8s/configmaps.yaml",
    "k8s/job.yaml",
]

# Required Terraform files for Databricks deployments.
# No K8s manifests or Dockerfile — Databricks manages its own compute.
DEFAULT_REQUIRED_DATABRICKS_TF_FILES = [
    "terraform/providers.tf",
    "terraform/main.tf",
    "terraform/variables.tf",
    "terraform/outputs.tf",
    "terraform/terraform.tfvars",
]

# LLM provider selection. Valid values: "openai", "anthropic", "vertexai"
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai")

# OpenAI HTTP timeout (seconds). Prevents unbounded hangs on slow/stuck API calls.
LLM_TIMEOUT_SEC = float(os.getenv("LLM_TIMEOUT_SEC", "120"))
LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "1"))
