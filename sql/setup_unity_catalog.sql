CREATE CATALOG IF NOT EXISTS multi_cloud_agent_workspace;
CREATE SCHEMA IF NOT EXISTS multi_cloud_agent_workspace.raw;

CREATE TABLE IF NOT EXISTS multi_cloud_agent_workspace.raw.pipe_sales_dbx_pipeline_data_lakehouse (
    order_id TEXT,
    unit_price DOUBLE PRECISION,
    quantity DOUBLE PRECISION,
    order_date TEXT,
    currency TEXT,
    run_date STRING
) USING DELTA
PARTITIONED BY (run_date);

CREATE TABLE IF NOT EXISTS multi_cloud_agent_workspace.raw.pipe_sales_dbx_pipeline_data_lakehouse_audit (
    run_timestamp TIMESTAMP,
    run_date STRING,
    rows_processed BIGINT,
    rows_rejected BIGINT,
    duration_seconds DOUBLE,
    rejected_by_reason MAP<STRING, BIGINT>
) USING DELTA;