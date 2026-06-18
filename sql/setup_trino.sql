CREATE SCHEMA IF NOT EXISTS hive.marketing_global;
DROP TABLE IF EXISTS hive.marketing_global.pipe_mysqp_to_gcp_etl_pipeline_to_gcp;
CREATE TABLE hive.marketing_global.pipe_mysqp_to_gcp_etl_pipeline_to_gcp (
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
    external_location = 'gs://mysqp-to-gcp-etl-pipeline-insights-data-dcdac2/processed/',
    partitioned_by = ARRAY['run_date']
);