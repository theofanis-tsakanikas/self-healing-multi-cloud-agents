CREATE SCHEMA IF NOT EXISTS hive.crm_us;
DROP TABLE IF EXISTS hive.crm_us.pipe_crm_us_to_azure;
CREATE TABLE hive.crm_us.pipe_crm_us_to_azure (
    cust_id BIGINT,
    full_name VARCHAR,
    email_address VARCHAR,
    phone_number VARCHAR,
    is_suspicious BOOLEAN,
    run_date DATE
) WITH (
    format = 'PARQUET',
    external_location = 'abfss://us-crm-insights-data@uscrminsightsstorage.dfs.core.windows.net/processed/',
    partitioned_by = ARRAY['run_date']
);