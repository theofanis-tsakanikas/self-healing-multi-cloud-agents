CREATE SCHEMA IF NOT EXISTS hive.marketing_global;
DROP TABLE IF EXISTS hive.marketing_global.pipe_mkt_global_to_gcp;
CREATE TABLE hive.marketing_global.pipe_mkt_global_to_gcp (
    campaign_id VARCHAR,
    platform_name VARCHAR,
    ad_spend DECIMAL(18,2),
    clicks BIGINT,
    impressions DOUBLE,
    event_timestamp TIMESTAMP,
    is_suspicious BOOLEAN,
    run_date DATE
) WITH (
    format = 'PARQUET',
    external_location = 'gs://global-marketing-insights-data/processed/',
    partitioned_by = ARRAY['run_date']
);