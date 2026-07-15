"""Golden-file + invariant tests for the deterministic artifact generators.

Goldens in tests/goldens/v1.0.0/ are the REAL artifacts from the validated
end-to-end runs (preserved at the v1.0.0 tag). Deliberate deltas from the goldens
(each one a standards change that post-dates the tag) are applied explicitly in the
tests so any OTHER difference fails:

  - Dockerfile:        python:3.11-slim -> 3.12-slim (version alignment)
  - grafana deploy:    + GF_SECURITY_ADMIN_PASSWORD from the grafana-admin Secret
  - monitoring_specs:  - the top-level "alerting" object (not Grafana 9+ schema)
  - databricks wf:     setup-cli pinned (was @main), permissions block,
                       scripts/pipe_*.py trigger (was scripts/**)
"""
import json
from pathlib import Path

import pytest
import yaml

from agents import codegen

GOLDEN = Path(__file__).parent / "goldens" / "v1.0.0"

# The GCP pipeline config the goldens were generated from (subset that codegen reads).
GCP_CONF = {
    "pipeline_id": "pipe_mkt_global_to_gcp",
    "project_name": "global-marketing-insights",
    "project_folder_name": "multi-cloud-self-healing-agent",
    "cloud_provider": "gcp",
    "project_structure": {"python_script_path": "scripts/pipe_mkt_global_to_gcp.py"},
    "gcp_setup": {
        "bucket_name": "global-marketing-insights-data",
        "region": "europe-west3",
        "project_id_env": "GCP_PROJECT_ID",
        "service_account_id": "global-mkt-insights-sa",
        "k8s_service_account_name": "global-mkt-insights-sa",
        "artifact_registry_region": "europe-west3",
        "gke_cluster_name": "multi-cloud-agent-gke",
    },
}

AWS_CONF = {
    "pipeline_id": "pipe_eu_sales_to_s3",
    "project_name": "eu-sales-insights",
    "project_folder_name": "multi-cloud-self-healing-agent",
    "cloud_provider": "aws",
    "aws_setup": {
        "aws_account_id": "000000000000",
        "bucket_name": "eu-sales-insights-data",
        "eks_cluster_name": "multi-cloud-agent-cluster",
        "iam_role_name": "eu-sales-insights-role",
        "k8s_service_account_name": "eu-sales-insights-sa",
        "region": "eu-central-1",
    },
}

AZURE_CONF = {
    "pipeline_id": "pipe_crm_us_to_azure",
    "project_name": "us-crm-insights",
    "project_folder_name": "multi-cloud-self-healing-agent",
    "cloud_provider": "azure",
    "azure_setup": {
        "storage_account_name": "uscrminsightsstorage",
        "container_name": "us-crm-insights-data",
        "resource_group_name": "multi-cloud-agent-rg",
        "aks_cluster_name": "multi-cloud-agent-aks",
        "k8s_service_account_name": "us-crm-insights-sa",
        "acr_login_server": "mcselfhealagentacr.azurecr.io",
    },
}

# The golden job.yaml's image (run-specific) — minus the tag it is the registry URL.
GCP_REGISTRY = ("europe-west3-docker.pkg.dev/multi-cloud-self-healing-agent/"
                "multi-cloud-agent-repo/pipe-mkt-global-to-gcp-20260606-0505")


def _docs(text: str):
    return [d for d in yaml.safe_load_all(text) if d is not None]


def _strip_data_whitespace(doc):
    """Normalize ConfigMap data values: YAML literal vs quoted scalars differ only
    in trailing-newline treatment after parsing."""
    if isinstance(doc, dict) and doc.get("kind") == "ConfigMap":
        for k, v in (doc.get("data") or {}).items():
            if isinstance(v, str):
                doc["data"][k] = v.rstrip("\n")
    return doc


class TestDockerfile:
    def test_matches_golden_modulo_python_version(self):
        golden = (GOLDEN / "Dockerfile").read_text()
        rendered = codegen.render_dockerfile("scripts/pipe_mkt_global_to_gcp.py")
        assert rendered == golden.replace("python:3.11-slim", "python:3.12-slim")


class TestRequirements:
    """The v1.0.0 requirements.txt is NOT a golden — it is a databricks-run leftover
    holding only the shared block. The spec is python_standards.md: shared + cloud
    storage/filesystem drivers + the SOURCE engine's DB driver."""

    def test_gcp_mysql(self):
        lines = codegen.render_requirements("gcp", "mysql").splitlines()
        assert lines[:5] == ["pandas", "sqlalchemy", "pyarrow", "trino", "prometheus-client"]
        assert "google-cloud-storage" in lines and "gcsfs" in lines and "pymysql" in lines
        assert "boto3" not in lines and "psycopg2-binary" not in lines

    def test_aws_postgres(self):
        lines = codegen.render_requirements("aws", "postgres").splitlines()
        assert "boto3" in lines and "s3fs" in lines and "psycopg2-binary" in lines

    def test_azure_postgres(self):
        lines = codegen.render_requirements("azure", "postgres").splitlines()
        assert "azure-storage-blob" in lines and "adlfs" in lines and "psycopg2-binary" in lines


class TestMonitoringSpecs:
    def test_matches_golden_modulo_alerting(self):
        golden = json.loads((GOLDEN / "dashboards" / "monitoring_specs.json").read_text())
        golden.pop("alerting")  # deliberately no longer emitted (not Grafana 9+ schema)
        rendered = json.loads(codegen.render_monitoring_specs(GCP_CONF, "gcp"))
        assert rendered == golden

    def test_uid_stable_and_slug_derived(self):
        spec = json.loads(codegen.render_monitoring_specs(AWS_CONF, "aws"))
        assert spec["uid"] == "eu-sales-data-observability"
        assert spec["tags"] == ["data-pipeline", "eu-sales", "aws"]


class TestLakeview:
    def test_matches_golden(self):
        golden = (GOLDEN / "dashboards" / "pipe_sales_lakehouse_lakeview.json").read_text()
        rendered = codegen.render_lakeview_dashboard(
            "multi_cloud_agent_workspace", "raw", "pipe_sales_lakehouse")
        assert json.loads(rendered) == json.loads(golden)


class TestK8sGoldens:
    """Each render vs the validated GCP golden, deltas applied explicitly."""

    def test_namespaces(self, monkeypatch):
        monkeypatch.setenv("GCP_PROJECT_ID", "multi-cloud-self-healing-agent")
        golden = _docs((GOLDEN / "k8s" / "00_namespaces.yaml").read_text())
        rendered = _docs(codegen.render_namespaces(GCP_CONF, "gcp"))
        assert rendered == golden

    def test_trino(self):
        golden = _docs((GOLDEN / "k8s" / "trino_deployment.yaml").read_text())
        rendered = _docs(codegen.render_trino_deployment(GCP_CONF, "gcp"))
        assert rendered == golden

    def test_prometheus(self):
        golden = _docs((GOLDEN / "k8s" / "prometheus_deployment.yaml").read_text())
        rendered = _docs(codegen.render_prometheus_deployment(GCP_CONF, "gcp"))
        assert rendered == golden

    def test_grafana_modulo_admin_password(self):
        golden = _docs((GOLDEN / "k8s" / "grafana_deployment.yaml").read_text())
        rendered = _docs(codegen.render_grafana_deployment(GCP_CONF, "gcp"))
        env = rendered[0]["spec"]["template"]["spec"]["containers"][0]["env"]
        admin = [e for e in env if e["name"] == "GF_SECURITY_ADMIN_PASSWORD"]
        assert admin and admin[0]["valueFrom"]["secretKeyRef"]["name"] == "grafana-admin"
        env.remove(admin[0])  # delta: golden predates the grafana-admin hardening
        assert rendered == golden

    def test_grafana_aws_gets_nlb_annotation_gcp_does_not(self):
        aws_svc = _docs(codegen.render_grafana_deployment(AWS_CONF, "aws"))[1]
        gcp_svc = _docs(codegen.render_grafana_deployment(GCP_CONF, "gcp"))[1]
        assert aws_svc["metadata"]["annotations"][
            "service.beta.kubernetes.io/aws-load-balancer-scheme"] == "internet-facing"
        assert "annotations" not in gcp_svc["metadata"]

    def test_job_matches_golden(self):
        golden = _docs((GOLDEN / "k8s" / "job.yaml").read_text())
        rendered = _docs(codegen.render_job(GCP_CONF, "gcp", GCP_REGISTRY))
        assert rendered == golden

    def test_job_azure_carries_workload_identity_label(self):
        doc = _docs(codegen.render_job(AZURE_CONF, "azure", "mcselfhealagentacr.azurecr.io/pipe-crm-us-to-azure"))[0]
        labels = doc["spec"]["template"]["metadata"]["labels"]
        assert labels["azure.workload.identity/use"] == "true"
        env = {e["name"]: e.get("value") for e in doc["spec"]["template"]["spec"]["containers"][0]["env"]}
        assert env["DESTINATION_URI"] == (
            "abfss://us-crm-insights-data@uscrminsightsstorage.dfs.core.windows.net/processed/")

    def test_configmaps_match_golden(self, tmp_path, monkeypatch):
        golden_text = (GOLDEN / "k8s" / "configmaps.yaml").read_text()
        golden = [_strip_data_whitespace(d) for d in _docs(golden_text)]
        # The two embeds come from the architect's files on disk — recreate them from
        # the golden's own embedded copies so the comparison closes the loop.
        sql = [d for d in golden if d["metadata"]["name"] == "trino-sql-config"][0]["data"]["setup_trino.sql"]
        specs = [d for d in golden if d["metadata"]["name"] == "grafana-dash-config"][0]["data"]["monitoring_specs.json"]
        monkeypatch.chdir(tmp_path)
        (tmp_path / "sql").mkdir()
        (tmp_path / "dashboards").mkdir()
        (tmp_path / "sql" / "setup_trino.sql").write_text(sql)
        (tmp_path / "dashboards" / "monitoring_specs.json").write_text(specs)
        rendered = [_strip_data_whitespace(d) for d in _docs(codegen.render_configmaps(GCP_CONF, "gcp"))]
        assert rendered == golden

    def test_hive_properties_per_cloud(self):
        aws = codegen._hive_properties(AWS_CONF, "aws")
        assert "hive.metastore=glue" in aws and "hive.metastore.glue.region=eu-central-1" in aws
        az = codegen._hive_properties(AZURE_CONF, "azure")
        assert "__ABFS_KEY__" in az and "hive.azure.abfs-storage-account=uscrminsightsstorage" in az
        gcp = codegen._hive_properties(GCP_CONF, "gcp")
        assert "hive.gcs.use-access-token=false" in gcp


class TestWorkflows:
    def test_databricks_matches_golden_modulo_hardening(self):
        golden = (GOLDEN / "workflows" / "pipe_sales_lakehouse_pipeline.yml").read_text()
        rendered = codegen.render_workflow(
            {"pipeline_id": "pipe_sales_lakehouse"}, "aws", "", is_databricks=True)
        # Deliberate delta (post-tag standards change): narrower trigger paths + a permissions block.
        # (The golden's actions are now SHA-pinned in-file to match the render — round-3 supply-chain
        # hardening; the generated deploy workflow carries all cloud credentials.)
        golden = golden.replace(
            "paths: ['scripts/**', 'sql/**', 'terraform/**']",
            "paths: ['scripts/pipe_*.py', 'sql/**', 'terraform/**']\n\npermissions:\n  contents: read")
        # Compare structure, not bytes (comment/whitespace differences are fine).
        g, r = yaml.safe_load(golden), yaml.safe_load(rendered)
        assert r["permissions"] == {"contents": "read"}
        assert r[True]["push"]["paths"] == g[True]["push"]["paths"]  # YAML parses `on` as True
        assert r["jobs"]["deploy"]["env"] == g["jobs"]["deploy"]["env"]
        g_steps = [(s.get("name"), s.get("uses")) for s in g["jobs"]["deploy"]["steps"]]
        r_steps = [(s.get("name"), s.get("uses")) for s in r["jobs"]["deploy"]["steps"]]
        assert r_steps == g_steps
        # The job-trigger script — post-tag delta (2026-06-19): the v1.0.0 golden blocked on a
        # bare `run-now` (the unified CLI now WAITS by default → empty RUN_ID on failure) and only
        # printed the generic task STATE, so the real Spark error never reached the Medic. The
        # render now (a) uses --no-wait to capture the run_id, and (b) prints `get-run-output` on
        # failure so the root-cause traceback surfaces in the CI log for the Medic to route.
        r_run = [s["run"] for s in r["jobs"]["deploy"]["steps"] if s.get("name") == "Trigger job run and wait"][0]
        assert "run-now" in r_run and "--no-wait" in r_run          # reliable run_id capture
        assert "get-run-output" in r_run                            # surfaces the real task error
        assert ".error" in r_run and ".error_trace" in r_run        # exception + traceback
        assert "INTERNAL_ERROR" in r_run                            # both terminal-failure states handled

    @pytest.mark.parametrize("conf,cloud,registry", [
        (AWS_CONF, "aws", "000000000000.dkr.ecr.eu-central-1.amazonaws.com/eu-sales-pipeline-repo"),
        (AZURE_CONF, "azure", "mcselfhealagentacr.azurecr.io/pipe-crm-us-to-azure"),
        (GCP_CONF, "gcp", GCP_REGISTRY),
    ])
    def test_k8s_workflow_invariants(self, conf, cloud, registry):
        wf = codegen.render_workflow(conf, cloud, registry, is_databricks=False)
        parsed = yaml.safe_load(wf)
        # Least privilege + narrowed trigger (never scripts/**)
        assert parsed["permissions"] == {"contents": "read"}
        assert "scripts/pipe_*.py" in parsed[True]["push"]["paths"]
        assert "scripts/**" not in parsed[True]["push"]["paths"]
        steps = [s.get("name") for s in parsed["jobs"]["deploy"]["steps"]]
        # Grafana admin secret BEFORE shared services; final heartbeat present.
        assert steps.index("Create Grafana Admin Secret") < steps.index("Deploy Shared Services to Kubernetes")
        assert "Deploy Pipeline Job to Kubernetes" in steps and "Check Deployment Status" in steps
        last_run = parsed["jobs"]["deploy"]["steps"][-1]["run"]
        assert 'echo "Deployment Complete"' in last_run
        body = wf
        if cloud == "aws":
            # The region must be the GitHub Variable — never a literal in the
            # aws-region parameter (the ECR URL itself legitimately contains it).
            assert "aws-region: ${{ vars.AWS_DEFAULT_REGION }}" in body
            assert "aws-region: eu-central-1" not in body
            assert "Set Image Tag in Job Manifest" in steps
        if cloud == "azure":
            assert "__ABFS_KEY__" in body and "az acr login" in body
            assert "AZURE_STORAGE_CONNECTION_STRING" in body
        if cloud == "gcp":
            # :latest contract — no tag-rewrite sed on GCP.
            assert "Set Image Tag in Job Manifest" not in steps
            assert "gke-gcloud-auth-plugin" in body
            assert f"{registry}:latest" in body

    def test_gcp_workflow_never_puts_expressions_in_manifests(self):
        """The sed-free GCP contract: job.yaml must carry :latest, never ${{ }}."""
        job = codegen.render_job(GCP_CONF, "gcp", GCP_REGISTRY)
        assert "${{" not in job and job.count(":latest") == 1


class TestEnsureOrchestrators:
    """Behavioral: writes + validates via the REAL validate_generated_code safety net."""

    def test_architect_artifacts_object_storage(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        written, errors = codegen.ensure_architect_artifacts(
            GCP_CONF, {"db_type": "mysql"}, {"provider": "kubernetes"}, [])
        assert errors == []
        assert sorted(written) == ["dashboards/monitoring_specs.json", "requirements.txt"]
        assert (tmp_path / "requirements.txt").exists()
        # Idempotent: nothing re-written when already tracked.
        again, _ = codegen.ensure_architect_artifacts(
            GCP_CONF, {"db_type": "mysql"}, {"provider": "kubernetes"}, written)
        assert again == []

    def test_architect_artifacts_databricks(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        conf = {"pipeline_id": "pipe_sales_lakehouse",
                "databricks_target": {"catalog": "c", "schema": "s", "table_name": "t"}}
        written, errors = codegen.ensure_architect_artifacts(
            conf, {}, {"provider": "databricks"}, [])
        assert errors == []
        assert written == ["dashboards/pipe_sales_lakehouse_lakeview.json"]

    def test_infra_artifacts_object_storage(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("GCP_PROJECT_ID", "multi-cloud-self-healing-agent")
        # main.py sets CLOUD_PROVIDER for the run; the validator's aws-only LB-annotation
        # rule gates on it.
        monkeypatch.setenv("CLOUD_PROVIDER", "gcp")
        monkeypatch.setattr(codegen, "REPO_ROOT", tmp_path)
        # The configmap embeds need the architect artifacts on disk first.
        (tmp_path / "sql").mkdir()
        (tmp_path / "dashboards").mkdir()
        (tmp_path / "sql" / "setup_trino.sql").write_text("SELECT 1;")
        (tmp_path / "dashboards" / "monitoring_specs.json").write_text(
            codegen.render_monitoring_specs(GCP_CONF, "gcp"))
        written, errors = codegen.ensure_infra_artifacts(
            GCP_CONF, {"provider": "kubernetes"}, GCP_REGISTRY, [])
        assert errors == []
        assert "Dockerfile" in written
        assert "k8s/job.yaml" in written and "k8s/configmaps.yaml" in written
        assert ".github/workflows/pipe_mkt_global_to_gcp_pipeline.yml" in written
        assert len([f for f in written if f.startswith("k8s/")]) == 6

    def test_infra_artifacts_databricks_only_workflow(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(codegen, "REPO_ROOT", tmp_path)
        written, errors = codegen.ensure_infra_artifacts(
            {"pipeline_id": "pipe_sales_lakehouse", "cloud_provider": "aws"},
            {"provider": "databricks"}, "", [])
        assert errors == []
        assert written == [".github/workflows/pipe_sales_lakehouse_pipeline.yml"]
        assert not (tmp_path / "Dockerfile").exists()
        assert not (tmp_path / "k8s").exists()


class TestImageContract:
    """The CI sed contract: the job.yaml image and the workflow's build/push/sed target
    MUST be byte-identical (host/image). This is the integration invariant the Azure
    bare-host bug violated — infra_node now appends the image segment for azure, so
    every render consumes the same full reference."""

    @pytest.mark.parametrize("conf,cloud,registry", [
        (AWS_CONF, "aws", "000000000000.dkr.ecr.eu-central-1.amazonaws.com/eu-sales-pipeline-repo"),
        (AZURE_CONF, "azure", "mcselfhealagentacr.azurecr.io/pipe-crm-us-to-azure"),
        (GCP_CONF, "gcp", GCP_REGISTRY),
    ])
    def test_job_image_equals_workflow_build_target(self, conf, cloud, registry):
        job = _docs(codegen.render_job(conf, cloud, registry))[0]
        job_image = job["spec"]["template"]["spec"]["containers"][0]["image"]
        assert job_image == f"{registry}:latest"
        assert "/" in job_image.rsplit(":", 1)[0], "image must carry the image segment, not a bare host"

        wf = codegen.render_workflow(conf, cloud, registry, is_databricks=False)
        assert f"docker build -t {registry}:" in wf
        assert f"docker push {registry}:" in wf
        if cloud in ("aws", "azure"):
            # the tag-rewrite sed must anchor on the EXACT image the job carries
            assert f"image: {registry}" in wf

    def test_azure_acr_login_uses_bare_host_label(self):
        wf = codegen.render_workflow(
            AZURE_CONF, "azure", "mcselfhealagentacr.azurecr.io/pipe-crm-us-to-azure",
            is_databricks=False)
        assert "echo 'mcselfhealagentacr.azurecr.io' | cut -d'.' -f1" in wf


class TestStaleWorkflowCleanup:
    """One pipeline's artifact set at a time: generating pipeline X's workflow must
    remove other pipe_*_pipeline.yml files (their triggers match X's shared artifact
    paths and would deploy the wrong cloud), and must never touch repo workflows."""

    def test_removes_other_pipelines_keeps_repo_workflows(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(codegen, "REPO_ROOT", tmp_path)
        wf_dir = tmp_path / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "pipe_eu_sales_to_s3_pipeline.yml").write_text("stale")
        (wf_dir / "run_agent.yml").write_text("repo infra — must survive")
        (wf_dir / "tests.yml").write_text("repo infra — must survive")

        written, errors = codegen.ensure_infra_artifacts(
            {"pipeline_id": "pipe_sales_lakehouse", "cloud_provider": "aws"},
            {"provider": "databricks"}, "", [])
        assert errors == []
        assert not (wf_dir / "pipe_eu_sales_to_s3_pipeline.yml").exists()
        assert (wf_dir / "pipe_sales_lakehouse_pipeline.yml").exists()
        assert (wf_dir / "run_agent.yml").exists()
        assert (wf_dir / "tests.yml").exists()
