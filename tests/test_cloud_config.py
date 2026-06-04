"""Unit tests for utils.cloud_config.cloud_get / cloud_get_infra.

The three-tier resolver (SSM → .bootstrap_outputs.json → env var) is the single
gate for every DB credential in the system. We mock all three sources:
  - SSM via monkeypatching `_try_ssm` (never imports boto3 / hits AWS),
  - the bootstrap file via monkeypatching `_load_bootstrap_outputs`,
  - env vars via monkeypatch.setenv.
"""
import utils.cloud_config as cc


def _no_ssm(monkeypatch):
    monkeypatch.setattr(cc, "_try_ssm", lambda cloud, key: None)


def _no_bootstrap(monkeypatch):
    monkeypatch.setattr(cc, "_load_bootstrap_outputs", lambda: {})


def _clear_db_env(monkeypatch):
    for var in (
        "POSTGRES_DB_HOST", "MYSQL_DB_HOST", "CRM_DB_HOST",
        "POSTGRES_DB_PASSWORD", "MYSQL_DB_PASSWORD", "CRM_DB_PASSWORD",
    ):
        monkeypatch.delenv(var, raising=False)


class TestCloudGetTierOrder:
    """Tier precedence: SSM (aws) > bootstrap_outputs > env var."""

    def test_aws_ssm_wins_over_bootstrap_and_env(self, monkeypatch):
        monkeypatch.setattr(cc, "_try_ssm", lambda cloud, key: "ssm-host")
        monkeypatch.setattr(cc, "_load_bootstrap_outputs",
                            lambda: {"aws": {"db_host": "bootstrap-host"}})
        monkeypatch.setenv("POSTGRES_DB_HOST", "env-host")
        assert cc.cloud_get("aws", "db_host", db_type="postgres") == "ssm-host"

    def test_aws_falls_back_to_bootstrap_when_ssm_misses(self, monkeypatch):
        _no_ssm(monkeypatch)
        monkeypatch.setattr(cc, "_load_bootstrap_outputs",
                            lambda: {"aws": {"db_host": "bootstrap-host"}})
        monkeypatch.setenv("POSTGRES_DB_HOST", "env-host")
        assert cc.cloud_get("aws", "db_host", db_type="postgres") == "bootstrap-host"

    def test_aws_falls_back_to_env_when_ssm_and_bootstrap_miss(self, monkeypatch):
        _no_ssm(monkeypatch)
        _no_bootstrap(monkeypatch)
        monkeypatch.setenv("POSTGRES_DB_HOST", "env-host")
        assert cc.cloud_get("aws", "db_host", db_type="postgres") == "env-host"

    def test_returns_none_when_nothing_resolves(self, monkeypatch):
        _no_ssm(monkeypatch)
        _no_bootstrap(monkeypatch)
        _clear_db_env(monkeypatch)
        assert cc.cloud_get("aws", "db_host", db_type="postgres") is None


class TestSsmKeyCandidates:
    """Legacy SSM param names (rds_host …) are tried when the new name misses."""

    def test_legacy_rds_host_resolved_via_candidates(self, monkeypatch):
        seen = []

        def fake_ssm(cloud, key):
            seen.append(key)
            return "legacy-host" if key == "rds_host" else None

        monkeypatch.setattr(cc, "_try_ssm", fake_ssm)
        _no_bootstrap(monkeypatch)
        result = cc.cloud_get("aws", "db_host", db_type="postgres")
        assert result == "legacy-host"
        # new name attempted first, legacy name second
        assert seen == ["db_host", "rds_host"]


class TestUseSsmFlag:
    def test_use_ssm_false_skips_ssm_on_aws(self, monkeypatch):
        called = {"ssm": False}

        def fake_ssm(cloud, key):
            called["ssm"] = True
            return "ssm-host"

        monkeypatch.setattr(cc, "_try_ssm", fake_ssm)
        monkeypatch.setattr(cc, "_load_bootstrap_outputs",
                            lambda: {"aws": {"db_host": "bootstrap-host"}})
        result = cc.cloud_get("aws", "db_host", db_type="postgres", use_ssm=False)
        assert result == "bootstrap-host"
        assert called["ssm"] is False


class TestNonAwsEnvDirect:
    """GCP/Azure never touch SSM — they read env vars via _ENV_FALLBACKS."""

    def test_gcp_mysql_reads_mysql_db_env(self, monkeypatch):
        _no_bootstrap(monkeypatch)
        monkeypatch.setenv("MYSQL_DB_HOST", "gcp-mysql-host")
        assert cc.cloud_get("gcp", "db_host", db_type="mysql") == "gcp-mysql-host"

    def test_azure_postgres_reads_crm_db_env(self, monkeypatch):
        _no_bootstrap(monkeypatch)
        monkeypatch.setenv("CRM_DB_PASSWORD", "azure-secret")
        assert cc.cloud_get("azure", "db_password", db_type="postgres") == "azure-secret"

    def test_gcp_never_calls_ssm(self, monkeypatch):
        called = {"ssm": False}
        monkeypatch.setattr(cc, "_try_ssm",
                            lambda c, k: called.__setitem__("ssm", True))
        _no_bootstrap(monkeypatch)
        monkeypatch.setenv("MYSQL_DB_HOST", "gcp-mysql-host")
        cc.cloud_get("gcp", "db_host", db_type="mysql")
        assert called["ssm"] is False


class TestDbTypeSelectsEnvVar:
    """db_type picks the right env-var family so engines never get mixed up."""

    def test_postgres_vs_mysql_resolve_distinct_vars(self, monkeypatch):
        _no_ssm(monkeypatch)
        _no_bootstrap(monkeypatch)
        monkeypatch.setenv("POSTGRES_DB_HOST", "pg-host")
        monkeypatch.setenv("MYSQL_DB_HOST", "my-host")
        assert cc.cloud_get("aws", "db_host", db_type="postgres") == "pg-host"
        assert cc.cloud_get("aws", "db_host", db_type="mysql") == "my-host"


class TestLegacyKeyMap:
    def test_caller_passing_rds_host_resolves_env(self, monkeypatch):
        _no_ssm(monkeypatch)
        _no_bootstrap(monkeypatch)
        monkeypatch.setenv("POSTGRES_DB_HOST", "env-host")
        # caller uses the old rds_host name → normalised to db_host for env lookup
        assert cc.cloud_get("aws", "rds_host", db_type="postgres") == "env-host"


class TestCloudGetInfra:
    def test_aws_ssm_value(self, monkeypatch):
        monkeypatch.setattr(cc, "_try_ssm", lambda c, k: "123.dkr.ecr.x.amazonaws.com/repo")
        assert cc.cloud_get_infra("aws", "ecr_repository_url").endswith("/repo")

    def test_bootstrap_fallback(self, monkeypatch):
        monkeypatch.setattr(cc, "_try_ssm", lambda c, k: None)
        monkeypatch.setattr(cc, "_load_bootstrap_outputs",
                            lambda: {"aws": {"ecr_repository_url": "url-from-bootstrap"}})
        assert cc.cloud_get_infra("aws", "ecr_repository_url") == "url-from-bootstrap"

    def test_returns_none_when_absent(self, monkeypatch):
        monkeypatch.setattr(cc, "_try_ssm", lambda c, k: None)
        monkeypatch.setattr(cc, "_load_bootstrap_outputs", lambda: {})
        assert cc.cloud_get_infra("aws", "ecr_repository_url") is None
