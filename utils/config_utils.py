import json
import os
import yaml


def load_yaml_file(path: str):
    with open(path, "r", encoding="utf-8") as file_obj:
        return yaml.safe_load(file_obj)


def load_pipeline_bundle(base_dir: str, pipeline_path: str):
    """
    Load pipeline config and its linked configs (source, rules, infra).
    """
    pipeline_conf = load_yaml_file(pipeline_path)

    source_path = os.path.join(base_dir, pipeline_conf["source_config"])
    rules_path = os.path.join(base_dir, pipeline_conf["business_rules_config"])
    infra_path = os.path.join(base_dir, pipeline_conf["target_infra_config"])

    db_conf = load_yaml_file(source_path)
    rules_conf = load_yaml_file(rules_path)
    infra_conf = load_yaml_file(infra_path)

    return pipeline_conf, db_conf, rules_conf, infra_conf


def build_architect_context(pipeline_conf: dict, db_conf: dict, rules_conf: dict, infra_conf: dict) -> str:
    """
    Unified context for the Architect. 
    Includes logical mappings for Data, Catalog (Trino), and Monitoring (Grafana).
    """
    # 1. Determine Cloud Provider & Protocol
    provider = pipeline_conf.get('cloud_provider', 'aws').lower()
    setup_key = f"{provider}_setup"
    cloud_setup = pipeline_conf.get(setup_key, {})
    
    if provider == 'aws':
        target_uri = f"s3://{cloud_setup.get('bucket_name')}/processed/"
    elif provider == 'gcp':
        target_uri = f"gs://{cloud_setup.get('bucket_name')}/processed/"
    else:  # azure
        container = cloud_setup.get('container_name')
        storage_account = cloud_setup.get('storage_account_name')
        target_uri = f"abfss://{container}@{storage_account}.dfs.core.windows.net/processed/"

    # 2. Retrieve Shared Services (Logical Metadata Only)
    shared = pipeline_conf.get('shared_services', {})
    trino_info = shared.get('trino', {})
    grafana_info = shared.get('grafana', {})

    # 3. Build the Agnostic Context
    context = {
        "PROJECT_METADATA": {
            "id": pipeline_conf.get("pipeline_id"),
            "domain": pipeline_conf.get("data_domain"),
            "cloud_provider": provider
        },
        "DATA_SOURCE": {
            "type": db_conf.get("db_type"),
            "table": db_conf.get("default_table"),
            "connection_vars": {k: v for k, v in db_conf.items() if k.startswith("env_var_")}
        },
        "TRANSFORMATION_LOGIC": rules_conf.get("quality_standards", []),
        "LOGICAL_DESTINATION": {
            "uri": target_uri,
            "format": infra_conf.get("data_format", "parquet"),
            "compression": infra_conf.get("compression_type", "snappy")
        },
        "CATALOG_AND_MONITORING": {
            "trino_metadata": {
                "catalog": trino_info.get("catalog"),
                "schema": trino_info.get("schema"),
                "table_name": trino_info.get("table_name", pipeline_conf.get("pipeline_id"))
            },
            "grafana_metadata": {
                "namespace": grafana_info.get("namespace")
            }
        }
    }
    
    return json.dumps(context, indent=2)

def build_infra_context(pipeline_conf: dict, infra_conf: dict) -> str:
    """
    Unified context for the Infra Engineer.
    Focuses on physical resource identifiers, cloud providers, 
    and orchestration metadata.
    """
    # 1. Cloud Provider Identification
    provider = pipeline_conf.get('cloud_provider', 'aws').lower()
    setup_key = f"{provider}_setup"
    cloud_setup = pipeline_conf.get(setup_key, {})
    
    # 2. Project & Folder Metadata (Essential for CI/CD paths)
    project_metadata = {
        "project_id": pipeline_conf.get("pipeline_id"),
        "project_folder_name": pipeline_conf.get("project_folder_name"),
        "target_cloud": provider,
        "region": cloud_setup.get("region")
    }

    # 3. Provider-Specific Infrastructure (The "Physical" layer)
    # Extract only the keys relevant for the target cloud
    infra_setup = {}
    if provider == 'aws':
        # ecr_repository_name is the repo name only (e.g. "eu-sales-pipeline-repo").
        # The full ECR URL (account ID + region) is returned by execute_terraform and
        # stored in state["ecr_repository_url"] — never in this static config.
        _ecr_raw = cloud_setup.get("ecr_repository_name") or cloud_setup.get("ecr_repository", "")
        _ecr_name = _ecr_raw.split("/")[-1] if "/" in _ecr_raw else _ecr_raw
        infra_setup = {
            "aws_account_id": cloud_setup.get("aws_account_id", ""),
            "bucket_name": cloud_setup.get("bucket_name"),
            "state_bucket": cloud_setup.get("state_bucket"),
            "state_key": cloud_setup.get("state_key"),
            "lock_table": cloud_setup.get("lock_table"),
            "eks_cluster_name": cloud_setup.get("eks_cluster_name"),
            "ecr_repository_name": _ecr_name,
            "ecr_repository_url": "RESOLVE_FROM_EXECUTE_TERRAFORM_OUTPUT",
            "iam_role_name": cloud_setup.get("iam_role_name"),
            "object_ownership": infra_conf.get("object_ownership", "BucketOwnerEnforced")
        }
    elif provider == 'azure':
        infra_setup = {
            "storage_account_name": cloud_setup.get("storage_account_name"),
            "container_name": cloud_setup.get("container_name"),
            "state_storage_account": cloud_setup.get("state_storage_account"),
            "state_container": cloud_setup.get("state_container"),
            "state_key": cloud_setup.get("state_key"),
            "aks_cluster_name": cloud_setup.get("aks_cluster_name"),
            "acr_login_server": cloud_setup.get("acr_login_server"),
            "managed_identity_name": cloud_setup.get("managed_identity_name"),
            "resource_group_name": cloud_setup.get("resource_group_name"),
            "subscription_id_env": cloud_setup.get("subscription_id_env"),
            "object_ownership": infra_conf.get("object_ownership", "private")
        }
    elif provider == 'gcp':
        infra_setup = {
            "bucket_name": cloud_setup.get("bucket_name"),
            "state_bucket": cloud_setup.get("state_bucket"),
            "state_prefix": cloud_setup.get("state_prefix"),
            "gke_cluster_name": cloud_setup.get("gke_cluster_name"),
            "artifact_registry_region": cloud_setup.get("artifact_registry_region"),
            "artifact_registry_repo": cloud_setup.get("artifact_registry_repo"),
            "service_account_id": cloud_setup.get("service_account_id"),
            "project_id_env": cloud_setup.get("project_id_env")
        }

    # 4. Orchestration Metadata (K8s / Identity)
    orchestration = {
        "k8s_namespace": cloud_setup.get("k8s_namespace", "default"),
        "service_account": cloud_setup.get("k8s_service_account_name"),
        "auth_method": infra_conf.get("auth_method") # e.g., "aws_iam_oidc_irsa"
    }

    # 5. Architect Artifact Paths (Reference for ConfigMaps & Docker)
    # The Infra agent needs to know WHERE the Architect saved the files
    artifacts = pipeline_conf.get("project_structure", {})

    # 6. Build the Final Infrastructure Summary
    infra_summary = {
        "PROJECT_METADATA": project_metadata,
        "CLOUD_SETUP": infra_setup,
        "ORCHESTRATION": orchestration,
        "ARTIFACT_PATHS": artifacts
    }
    
    return json.dumps(infra_summary, indent=2)

def build_databricks_infra_context(pipeline_conf: dict, infra_conf: dict) -> str:
    """
    Unified context for the Infra Engineer when provider is Databricks.
    Focuses on Databricks workspace, compute, storage, and Unity Catalog.
    """
    host_cloud = infra_conf.get("host_cloud", "aws").lower()
    setup_key = f"{host_cloud}_setup"
    cloud_setup = pipeline_conf.get(setup_key, {})

    # Resolve Delta storage URI based on host_cloud
    pipeline_id = pipeline_conf.get("pipeline_id", "pipeline")
    if host_cloud == "aws":
        delta_uri = f"s3://{cloud_setup.get('bucket_name')}/{pipeline_id}/"
    elif host_cloud == "azure":
        container = cloud_setup.get("container_name")
        account = cloud_setup.get("storage_account_name")
        delta_uri = f"abfss://{container}@{account}.dfs.core.windows.net/{pipeline_id}/"
    else:  # gcp
        delta_uri = f"gs://{cloud_setup.get('bucket_name')}/{pipeline_id}/"

    summary = {
        "PROJECT_METADATA": {
            "project_id": pipeline_id,
            "provider": "databricks",
            "host_cloud": host_cloud,
            "region": infra_conf.get("host_region", cloud_setup.get("region")),
            "workspace_name": infra_conf.get("workspace_name"),
            "databricks_tier": infra_conf.get("databricks_tier", "premium"),
        },
        "COMPUTE": {
            "jobs_cluster": infra_conf.get("jobs_cluster", {}),
            "sql_warehouse": infra_conf.get("sql_warehouse", {}),
        },
        "STORAGE": {
            "format": infra_conf.get("delta_storage", {}).get("format", "delta"),
            "delta_uri": delta_uri,
            "partition_by": infra_conf.get("delta_storage", {}).get("partition_by", "run_date"),
        },
        "UNITY_CATALOG": infra_conf.get("unity_catalog", {}),
        "MLFLOW": infra_conf.get("mlflow", {}),
        "CICD": {
            "databricks_cli_version": infra_conf.get("cicd", {}).get(
                "databricks_cli_version", "0.18.0"
            ),
            "runner": infra_conf.get("cicd", {}).get("runner", "ubuntu-latest"),
        },
        "TERRAFORM_BACKEND": infra_conf.get("terraform_backend", {}),
        "CLOUD_SETUP": {
            k: v for k, v in cloud_setup.items()
            if k in (
                "bucket_name", "state_bucket", "state_key",
                "storage_account_name", "container_name",
                "state_storage_account", "state_container",
                "resource_group_name", "subscription_id_env",
                "service_account_id", "project_id_env",
            )
        },
    }
    return json.dumps(summary, indent=2)


def load_matching_infra_contexts(infra_dir: str, target_infra: str):
    """
    Load all infra YAMLs matching target infrastructure name.
    """
    contexts = []
    if not (os.path.exists(infra_dir) and target_infra):
        return contexts

    for filename in os.listdir(infra_dir):
        if target_infra in filename and (filename.endswith(".yaml") or filename.endswith(".yml")):
            file_path = os.path.join(infra_dir, filename)
            conf = load_yaml_file(file_path)
            if conf:
                contexts.append((filename, conf))
    return contexts
