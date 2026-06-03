CREATE SCHEMA IF NOT EXISTS azure_catalog.crm_us;
DROP TABLE IF EXISTS azure_catalog.crm_us.pipe_crm_us_to_azure;
CREATE TABLE azure_catalog.crm_us.pipe_crm_us_to_azure (
    cust_id BIGINT,
    full_name TEXT,
    email_address TEXT,
    phone_number TEXT,
    is_suspicious BOOLEAN,
    run_date DATE
) WITH (
    format = 'PARQUET',
    external_location = 'abfss://us-crm-insights-data@uscrminsightsstorage.dfs.core.windows.net/processed/',
    partitioned_by = ARRAY['run_date']
);