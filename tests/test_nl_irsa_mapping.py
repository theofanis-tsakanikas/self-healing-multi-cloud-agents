"""
Regression: an NL/Streamlit-authored AWS pipeline must point its K8s ServiceAccount at
the SHARED pipelines IRSA role (bootstrap), not a per-slug role that was never created.
Without this the pod cannot assume any role → no SSM creds (cloud_get → None) → the
'host name "None"' failure. See bootstrap/aws/iam.tf:irsa_pipelines + _build_from_answers.
"""
from unittest.mock import patch

import utils.nlp_parser as nlp


_ANSWERS = {
    "pipeline_slug": "my_sales",
    "data_domain": "sales",
    "source_db_type": "postgres",
    "source_table": "raw_my_sales",
    "target_cloud": "aws",
    "frequency": "daily",
    "owner_team": "analytics_team",
}

_FAKE_OUTPUTS = {
    "aws": {
        "aws_account_id": "123456789012",
        "state_bucket": "multi-cloud-agent-tf-state-bucket",
        "lock_table": "terraform-state-lock",
        "eks_oidc_issuer": "https://oidc.eks.eu-central-1.amazonaws.com/id/ABC",
        "pipeline_irsa_role_name": "multi-cloud-pipelines-irsa",
        "pipeline_irsa_role_arn": "arn:aws:iam::123456789012:role/multi-cloud-pipelines-irsa",
        "oidc_provider_arn": "arn:aws:iam::123456789012:oidc-provider/oidc.eks...",
    }
}


def _aws_setup(outputs):
    # _build_from_answers writes the 4 generated config files to disk as a side effect;
    # stub it out so the test never pollutes the repo (configs/pipelines/, etc.).
    with patch.object(nlp, "_load_bootstrap_outputs", return_value=outputs), \
         patch.object(nlp, "_save_generated_configs"):
        pipeline_conf, *_ = nlp._build_from_answers(_ANSWERS, [])
    return pipeline_conf["aws_setup"]


def test_nl_aws_sa_points_at_shared_irsa_role():
    setup = _aws_setup(_FAKE_OUTPUTS)
    # SA annotation role is overridden to the real shared role (was a non-existent per-slug role)
    assert setup["iam_role_name"] == "multi-cloud-pipelines-irsa"
    assert setup["aws_account_id"] == "123456789012"
    # SA name stays on the convention the wildcard trust matches (*-insights-sa)
    assert setup["k8s_service_account_name"] == "my-sales-insights-sa"


def test_nl_aws_without_bootstrap_output_keeps_per_slug_role():
    # No shared-role output → no override (the Streamlit guard blocks the deploy upstream).
    outputs = {"aws": {k: v for k, v in _FAKE_OUTPUTS["aws"].items()
                       if k != "pipeline_irsa_role_name"}}
    setup = _aws_setup(outputs)
    assert setup["iam_role_name"] == "my-sales-insights-role"


_AZURE_ANSWERS = {**_ANSWERS, "target_cloud": "azure"}

_AZURE_OUTPUTS = {
    "azure": {
        "acr_login_server": "mcagent.azurecr.io",
        "aks_cluster_name": "mc-agent-aks",
        "aks_oidc_issuer_url": "https://oidc.prod-aks.azure.com/abc/",
        "state_storage_account": "mcagenttfstate",
        "state_container": "tfstate",
        "resource_group_name": "multi-cloud-agent-rg",
        "pipeline_managed_identity_name": "multi-cloud-pipelines-identity",
        "pipeline_managed_identity_client_id": "00000000-0000-0000-0000-000000000000",
    }
}


def _azure_setup(outputs):
    with patch.object(nlp, "_load_bootstrap_outputs", return_value=outputs), \
         patch.object(nlp, "_save_generated_configs"):
        pipeline_conf, *_ = nlp._build_from_answers(_AZURE_ANSWERS, [])
    return pipeline_conf["azure_setup"]


def test_nl_azure_sa_uses_fixed_shared_identity():
    setup = _azure_setup(_AZURE_OUTPUTS)
    # Azure federated subjects are exact (no wildcard) → one fixed SA + the shared identity.
    assert setup["managed_identity_name"] == "multi-cloud-pipelines-identity"
    assert setup["k8s_service_account_name"] == "pipelines-insights-sa"


def test_nl_azure_without_bootstrap_output_keeps_per_slug():
    outputs = {"azure": {k: v for k, v in _AZURE_OUTPUTS["azure"].items()
                         if k != "pipeline_managed_identity_name"}}
    setup = _azure_setup(outputs)
    assert setup["managed_identity_name"] == "my-sales-insights-identity"
    assert setup["k8s_service_account_name"] == "my-sales-insights-sa"


_GCP_ANSWERS = {**_ANSWERS, "target_cloud": "gcp", "source_db_type": "mysql"}

_GCP_OUTPUTS = {
    "gcp": {
        "project_id": "mc-agent-prod",
        "region": "europe-west1",
        "artifact_registry_url": "europe-west1-docker.pkg.dev/mc-agent-prod/repo",
        "state_bucket": "mc-agent-tfstate",
        "pipeline_service_account_email": "pipelines-insights-sa@mc-agent-prod.iam.gserviceaccount.com",
        "pipeline_service_account_id": "pipelines-insights-sa",
    }
}


def _gcp_setup(outputs):
    with patch.object(nlp, "_load_bootstrap_outputs", return_value=outputs), \
         patch.object(nlp, "_save_generated_configs"):
        pipeline_conf, *_ = nlp._build_from_answers(_GCP_ANSWERS, [])
    return pipeline_conf["gcp_setup"]


def test_nl_gcp_sa_uses_fixed_shared_service_account():
    setup = _gcp_setup(_GCP_OUTPUTS)
    # GCP WI binding members are exact (no wildcard) → one fixed KSA + the shared SA.
    assert setup["service_account_id"] == "pipelines-insights-sa"
    assert setup["service_account_email"] == (
        "pipelines-insights-sa@mc-agent-prod.iam.gserviceaccount.com"
    )
    assert setup["k8s_service_account_name"] == "pipelines-insights-sa"


def test_nl_gcp_without_bootstrap_output_keeps_per_slug():
    outputs = {"gcp": {k: v for k, v in _GCP_OUTPUTS["gcp"].items()
                       if k != "pipeline_service_account_id"}}
    setup = _gcp_setup(outputs)
    assert setup["k8s_service_account_name"] == "my-sales-insights-sa"
