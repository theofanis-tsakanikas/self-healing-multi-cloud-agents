import argparse
import datetime
import logging
import time

from pyspark.sql import SparkSession
from pyspark.sql import functions as F

logging.basicConfig(level=logging.INFO)
logging.getLogger("py4j").setLevel(logging.WARNING)
logging.getLogger("py4j.clientserver").setLevel(logging.WARNING)

def run():
    logging.info("Databricks pipeline starting: pipe_sales_orders_databricks_lakehouse")
    spark = SparkSession.builder.getOrCreate()

    # ── 1. PARAMETERS ─────────────────────
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--schema", required=True)
    parser.add_argument("--secret-scope", required=True)
    parser.add_argument("--db-host", required=True)
    parser.add_argument("--db-name", required=True)
    parser.add_argument("--db-user", required=True)
    args, _ = parser.parse_known_args()

    catalog, schema = args.catalog, args.schema
    table = f"{catalog}.{schema}.pipe_sales_orders_databricks_lakehouse"
    audit_table = f"{catalog}.{schema}.pipe_sales_orders_databricks_lakehouse_audit"
    run_date = datetime.date.today().isoformat()
    start_time = time.time()

    # ── 2. CREDENTIALS ─────────────────────
    db_host, db_name, db_user = args.db_host, args.db_name, args.db_user
    db_password = dbutils.secrets.get(args.secret_scope, "db_password")  # noqa: F821
    jdbc_url = f"jdbc:postgresql://{db_host}:5432/{db_name}?sslmode=require&connectTimeout=15&socketTimeout=120"

    # ── 3. IDEMPOTENCY ─────────────────────
    if spark.catalog.tableExists(table):
        already = spark.table(table).where(F.col("run_date") == run_date).limit(1).count()
        if already:
            logging.info(f"run_date={run_date} already present in {table}. Skipping.")
            return

    # ── 4. EXTRACT (Spark JDBC) ─────────────────────
    df = (
        spark.read.format("jdbc")
        .option("url", jdbc_url)
        .option("dbtable", "raw_sales_lakehouse")
        .option("user", db_user)
        .option("password", db_password)
        .option("driver", "org.postgresql.Driver")
        .load()
        .cache()
    )

    # ── 5. BUSINESS RULES ─────────────────────
    rejected_by_reason = {}

    # Rule 1: Unit price cannot be null, zero, or negative.
    _before = df.count()
    df = df.filter(F.col("unit_price").cast("double") > 0.0)
    rejected_by_reason["monetary_integrity"] = _before - df.count()

    # Rule 2: Order date cannot be in the future.
    _before = df.count()
    df = df.filter(F.col("order_date") <= F.current_timestamp())
    rejected_by_reason["temporal_validity"] = _before - df.count()

    # Rule 3: Order ID cannot be missing or empty.
    _before = df.count()
    df = df.filter(F.col("order_id").isNotNull() & (F.col("order_id") != ""))
    rejected_by_reason["order_id_integrity"] = _before - df.count()

    # Rule 4: Currency must be EUR or GBP; replace others with default EUR.
    df = df.withColumn("currency", F.when(~F.col("currency").isin(["EUR", "GBP"]), "EUR").otherwise(F.col("currency")))

    # Rule 5: Quantity of 1000 or more is suspicious.
    df = df.withColumn("is_suspicious", F.when(F.col("quantity") >= 1000, True).otherwise(False))

    # Rule 6: Quantity cannot be negative or zero.
    _before = df.count()
    df = df.filter(F.col("quantity") > 0)
    rejected_by_reason["quantity_integrity"] = _before - df.count()

    rows_rejected = sum(rejected_by_reason.values())

    # ── 6. WRITE to Delta ─────────────────────
    out = df.withColumn("run_date", F.lit(run_date))
    rows_processed = out.count()
    (
        out.write.format("delta")
        .mode("overwrite")
        .option("replaceWhere", f"run_date = '{run_date}'")
        .partitionBy("run_date")
        .saveAsTable(table)
    )

    duration_seconds = time.time() - start_time
    logging.info(f"Wrote {rows_processed} rows to {table} (rejected={rows_rejected}).")

    # ── 7. AUDIT TABLE ─────────────────────
    audit_row = [(
        datetime.datetime.now(datetime.timezone.utc),
        run_date,
        int(rows_processed),
        int(rows_rejected),
        float(duration_seconds),
        {k: int(v) for k, v in rejected_by_reason.items()},
    )]
    audit_cols = ["run_timestamp", "run_date", "rows_processed", "rows_rejected", "duration_seconds", "rejected_by_reason"]
    spark.createDataFrame(audit_row, audit_cols) \
        .write.format("delta").mode("append").saveAsTable(audit_table)
    logging.info(
        f"Audit row written: rows_processed={rows_processed}, rows_rejected={rows_rejected}, "
        f"duration={duration_seconds:.1f}s, by_reason={rejected_by_reason}"
    )


if __name__ == "__main__":
    run()