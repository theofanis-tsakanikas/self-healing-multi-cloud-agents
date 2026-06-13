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
    with patch.object(nlp, "_load_bootstrap_outputs", return_value=outputs):
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
