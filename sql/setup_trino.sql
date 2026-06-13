CREATE SCHEMA IF NOT EXISTS hive.sales_eu;
DROP TABLE IF EXISTS hive.sales_eu.pipe_etl_from_postgress_to_s3_to_s3;
CREATE TABLE hive.sales_eu.pipe_etl_from_postgress_to_s3_to_s3 (
    order_id VARCHAR,
    unit_price DECIMAL(18,2),
    quantity INTEGER,
    order_date TIMESTAMP,
    currency VARCHAR,
    is_suspicious BOOLEAN,
    run_date DATE
) WITH (
    format = 'PARQUET',
    external_location = 's3://etl-from-postgress-to-s3-insights-data/processed/',
    partitioned_by = ARRAY['run_date']
);
CALL hive.system.sync_partition_metadata('sales_eu', 'pipe_etl_from_postgress_to_s3_to_s3', 'ADD');