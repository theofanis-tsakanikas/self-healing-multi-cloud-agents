# STANDARD: TRINO DDL GENERATION
When generating `setup_trino.sql`, ensure the following:

- **Command**: Always emit statements in this exact order, using the **full 3-part name** (`catalog.schema.table`) in every statement. Never use a 2-part name — without the catalog prefix Trino cannot resolve the connector and will raise "Access Denied" or route to the wrong catalog:
    1. `CREATE SCHEMA IF NOT EXISTS {catalog}.{schema}` — schema must exist before the table.
    2. `DROP TABLE IF EXISTS {catalog}.{schema}.{table_name}` — ensures the table definition is always recreated fresh. External tables do not store data in Trino/Glue — dropping them only removes the metadata pointer, never the S3 data. This makes every deployment idempotent even if `external_location` changes between runs.
    3. `CREATE TABLE {catalog}.{schema}.{table_name} (...)` — plain `CREATE TABLE`, not `CREATE TABLE IF NOT EXISTS` (the preceding DROP makes IF NOT EXISTS redundant). Never use `CREATE EXTERNAL TABLE` — that is Hive/HQL syntax and is not valid in Trino.

    Concrete example (values from `CATALOG_AND_MONITORING.trino_metadata`):
    ```sql
    CREATE SCHEMA IF NOT EXISTS hive.sales_eu;
    DROP TABLE IF EXISTS hive.sales_eu.pipe_sales_eu_to_s3;
    CREATE TABLE hive.sales_eu.pipe_sales_eu_to_s3 ( ... );
    ```
- **Naming**: Use the full 3-part path: `{catalog}.{schema}.{table_name}` where all three values come from `CATALOG_AND_MONITORING.trino_metadata` in the context. Never invent catalog or schema names. `table_name` is the pipeline destination table (e.g. `pipe_sales_eu_to_s3`) — it is NEVER the source table from `DATA_SOURCE.table` (e.g. `raw_eu_sales`). These are two completely different tables: the source is in PostgreSQL, the destination is a Hive external table on S3.
- **Format**: Always set `WITH (format = 'PARQUET', external_location = '...')`.
- **external_location**: Use `LOGICAL_DESTINATION.uri` taken verbatim from the context — do NOT append `{project_id}/` or any session-specific suffix. The table must point to the stable parent prefix so it reads all date partitions as a unified dataset. The pipeline script writes to `run_date=YYYY-MM-DD/` subdirectories — the Trino table discovers these as Hive-style partitions via `partitioned_by = ARRAY['run_date']`.

  **Protocol MUST match the cloud provider — never substitute one for another:**

  | Cloud | Catalog name | Correct protocol | Example |
  |---|---|---|---|
  | AWS (Hive + Glue) | `hive` | `s3://` | `s3://eu-sales-insights-data/processed/` |
  | Azure (Hive + ABFS) | `azure_catalog` | `abfss://` | `abfss://us-crm-insights-data@uscrminsightsstorage.dfs.core.windows.net/processed/` |
  | GCP (Hive + GCS) | `gcp_catalog` | `gs://` | `gs://global-marketing-insights-data/processed/` |

  Trino's Hive connector supports `s3://` natively via the AWS S3 file system. With a GCS connector configured it supports `gs://`. With an Azure connector it supports `abfss://`. **Do NOT use `s3a://` (Hadoop/Spark protocol) for any cloud.** Do NOT replace `gs://` or `abfss://` with `s3://` — that would point to a non-existent AWS bucket.
- **Data Types**:
    - Column names and types MUST be derived from the schema returned by `read_data_schema` PLUS any columns added by business rules.
    - `is_suspicious BOOLEAN`: add this column **only if** at least one `FLAG_AS_SUSPICIOUS` rule is defined in `TRANSFORMATION_LOGIC`. If no such rule exists, omit the column entirely from the DDL — adding it without a corresponding pipeline implementation creates a schema/data mismatch. Do not add it as a default or placeholder.
    - Map discovered types as follows. **Preserve the type `read_data_schema` actually reports** — it returns the real SQL type per column (e.g. `cust_id (BIGINT)`), so use it directly instead of re-guessing from the column's meaning:
        - String / text → `VARCHAR`
        - Integer / count / quantity / **identifier** (e.g. `cust_id`, `customer_id`) → keep the discovered integer type: `INTEGER` stays `INTEGER`, `BIGINT` stays `BIGINT`. **NEVER downgrade an integer/BIGINT column to `VARCHAR`** just because it is an ID.
        - Floating-point / financial amount → `DECIMAL(18,2)`
        - Date-time → `TIMESTAMP`
        - Boolean → `BOOLEAN`
    - Do not apply `DECIMAL(18,2)` to quantity or count columns — reserve it for monetary values only. Example:
        ```sql
        unit_price DECIMAL(18,2),  -- ✅ monetary
        -- quantity DECIMAL(18,2)  ← ❌ WRONG — DECIMAL is for monetary only
        ```
- **Partitioning**: Always partition by `run_date`. Add `run_date DATE` as the **last column** in the column list and include `partitioned_by = ARRAY['run_date']` in the `WITH` clause. `run_date` is a Hive-style partition key — Trino derives its value from the storage path (`run_date=YYYY-MM-DD/`) and does NOT expect it inside the Parquet files.

  **`run_date` is ALWAYS the FINAL column — no exception.** When a `FLAG_AS_SUSPICIOUS` rule adds the optional `is_suspicious BOOLEAN` column (see Data Types above), `is_suspicious` is a regular data column and MUST be placed **before** `run_date`, never after it. Trino fails at runtime with `Partition keys must be the last columns in the table` if any non-partition column follows `run_date`:
    ```sql
    -- ❌ WRONG — is_suspicious after the partition key:
    ...  phone_number VARCHAR,  run_date DATE,  is_suspicious BOOLEAN
    -- ✅ CORRECT — partition key strictly last, is_suspicious before it:
    ...  phone_number VARCHAR,  is_suspicious BOOLEAN,  run_date DATE
    ```

  The same pattern works for all three clouds — only `external_location` changes:

    ```sql
    -- AWS
    CREATE TABLE hive.sales_eu.pipe_sales_eu_to_s3 (
        order_id    VARCHAR,
        unit_price  DECIMAL(18,2),
        quantity    INTEGER,
        run_date    DATE
    ) WITH (
        format            = 'PARQUET',
        external_location = 's3://eu-sales-insights-data/processed/',
        partitioned_by    = ARRAY['run_date']
    );

    -- Azure
    CREATE TABLE azure_catalog.crm_us.pipe_crm_us_to_azure (
        cust_id     INTEGER,
        full_name   VARCHAR,
        run_date    DATE
    ) WITH (
        format            = 'PARQUET',
        external_location = 'abfss://us-crm-insights-data@uscrminsightsstorage.dfs.core.windows.net/processed/',
        partitioned_by    = ARRAY['run_date']
    );

    -- GCP
    CREATE TABLE gcp_catalog.marketing_global.pipe_mkt_global_to_gcp (
        campaign_id VARCHAR,
        ad_spend    DECIMAL(18,2),
        run_date    DATE
    ) WITH (
        format            = 'PARQUET',
        external_location = 'gs://global-marketing-insights-data/processed/',
        partitioned_by    = ARRAY['run_date']
    );
    ```

- **Trino Partition Sync (all clouds)**: After the pipeline writes all chunks, call `sync_partition_metadata` so the new `run_date` partition is immediately queryable without manual `MSCK REPAIR`. The CALL syntax is identical for all catalogs — only the catalog name changes:
    ```sql
    CALL hive.system.sync_partition_metadata('sales_eu', 'pipe_sales_eu_to_s3', 'ADD')          -- AWS
    CALL azure_catalog.system.sync_partition_metadata('crm_us', 'pipe_crm_us_to_azure', 'ADD')  -- Azure
    CALL gcp_catalog.system.sync_partition_metadata('marketing_global', 'pipe_mkt_global_to_gcp', 'ADD')  -- GCP
    ```
    Use the catalog/schema/table values from `CATALOG_AND_MONITORING.trino_metadata` in the pipeline context.
