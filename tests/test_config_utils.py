from pathlib import Path

from utils.config_utils import load_pipeline_bundle

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PIPELINE_PATH = str(PROJECT_ROOT / "configs" / "pipelines" / "eu_sales_pipeline.yaml")


class TestLoadPipelineBundle:
    """Uses the real eu_sales_pipeline.yaml fixture — no filesystem mocking needed."""

    def setup_method(self):
        result = load_pipeline_bundle(str(PROJECT_ROOT), PIPELINE_PATH)
        self.pipe_conf, self.db_conf, self.rules_conf, self.infra_conf = result

    def test_returns_four_tuple(self):
        result = load_pipeline_bundle(str(PROJECT_ROOT), PIPELINE_PATH)
        assert len(result) == 4

    def test_pipeline_conf_pipeline_id(self):
        assert self.pipe_conf["pipeline_id"] == "pipe_eu_sales_to_s3"

    def test_pipeline_conf_cloud_provider(self):
        assert self.pipe_conf["cloud_provider"] == "aws"

    def test_db_conf_type_is_postgres(self):
        assert self.db_conf["db_type"] == "postgres"

    def test_db_conf_default_table(self):
        assert self.db_conf["default_table"] == "raw_eu_sales"

    def test_rules_conf_has_quality_standards_list(self):
        standards = self.rules_conf["quality_standards"]
        assert isinstance(standards, list)
        assert len(standards) > 0

    def test_infra_conf_cloud_provider_is_aws(self):
        assert self.infra_conf["cloud_provider"] == "aws"

    def test_infra_conf_data_format_is_parquet(self):
        assert self.infra_conf["data_format"] == "parquet"
