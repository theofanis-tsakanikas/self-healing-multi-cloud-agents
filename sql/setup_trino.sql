CREATE SCHEMA IF NOT EXISTS hive.marketing_global;
DROP TABLE IF EXISTS hive.marketing_global.pipe_etl_gcp_pipeline_to_gcp;
CREATE TABLE hive.marketing_global.pipe_etl_gcp_pipeline_to_gcp (
    campaign_id VARCHAR,
    platform_name VARCHAR,
    ad_spend DOUBLE,
    clicks BIGINT,
    impressions DOUBLE,
    event_timestamp TIMESTAMP,
    is_suspicious BOOLEAN,
    run_date DATE
) WITH (
    format = 'PARQUET',
    external_location = 'gs://etl-gcp-pipeline-insights-data-26c3da/processed/',
    partitioned_by = ARRAY['run_date']
);
CALL hive.system.sync_partition_metadata('marketing_global', 'pipe_etl_gcp_pipeline_to_gcp', 'ADD');