import os
import time
import datetime
import logging
from urllib.parse import urlparse

import pandas as pd
from sqlalchemy import create_engine
from trino.dbapi import connect as trino_connect
from prometheus_client import CollectorRegistry, Gauge, push_to_gateway

from utils.cloud_config import cloud_get  # SSM → bootstrap_outputs → env fallback

_CLOUD = os.getenv("CLOUD_PROVIDER", "aws")

logging.basicConfig(level=logging.INFO)


def run():
    logging.info("Pipeline starting: pipe_move_data_post_progress_to_adl_to_azure")

    # ── 1. IDEMPOTENCY CHECK ──────────────────────────────────────────────────
    run_date = datetime.date.today().isoformat()
    destination_uri = os.getenv("DESTINATION_URI")  # injected by K8s Job env
    partition_uri = f"{destination_uri}run_date={run_date}/"
    parsed = urlparse(partition_uri)
    bucket = parsed.netloc
    prefix = parsed.path.lstrip('/')

    if _CLOUD == "azure":
        from azure.storage.blob import BlobServiceClient
        container_name = bucket.split('@')[0]
        client = BlobServiceClient.from_connection_string(os.getenv('AZURE_STORAGE_CONNECTION_STRING'))
        container = client.get_container_client(container_name)
        blobs = list(container.list_blobs(name_starts_with=prefix))
        if blobs:
            logging.info("Destination already populated. Skipping.")
            return

    # ── 2. CREDENTIALS via cloud_get() ───────────────────────────────────────
    host = cloud_get("azure", "db_host",     db_type="postgres")
    port = cloud_get("azure", "db_port",     db_type="postgres") or "5432"
    user = cloud_get("azure", "db_user",     db_type="postgres")
    pw   = cloud_get("azure", "db_password", db_type="postgres")
    db   = cloud_get("azure", "db_name",     db_type="postgres")
    connection_string = f"postgresql+psycopg2://{user}:{pw}@{host}:{port}/{db}"

    # ── 3. EXTRACTION + TRANSFORMATION + WRITE (one try block) ───────────────
    start_time = time.time()   # for pipeline_duration_seconds metric
    total_rows = 0
    rejected_by_reason = {}    # rule_name → cumulative dropped rows (one entry per row-removing rule)
    query = "SELECT * FROM raw_us_crm"  # replace with actual table from context

    try:
        engine = create_engine(connection_string)
        for i, chunk in enumerate(pd.read_sql_query(query, engine, chunksize=1000)):

            # 3a. Date conversion — ONLY when the discovered schema actually HAS a date/
            #     timestamp column that a business rule compares against. If the table has no
            #     date column (e.g. a CRM customers table: id/name/email/phone), OMIT this
            #     step entirely. NEVER force pd.to_datetime on a non-date column (e.g. a name)
            #     — it raises ValueError / yields NaT and crashes the run.

            # 3b. Business rules — translate ALL quality_standards from pipeline config.
            #     NEVER use placeholder values like `is_suspicious = False`.
            #     Each row-removing rule takes its OWN FRESH `_before = len(chunk)` immediately
            #     before ITS filter and accumulates the delta under its own quality_standards
            #     rule name (the `reason` keys come from config — NEVER hardcoded literals).
            #     A single shared `_before` captured once at the top double-counts — see the
            #     Worked Example above.

            #   DROP_RECORD:      _before = len(chunk)
            #                     chunk = chunk[condition]
            #                     rejected_by_reason['<rule_name>'] = \
            #                         rejected_by_reason.get('<rule_name>', 0) + (_before - len(chunk))
            #   EXCLUDE_AND_LOG:  _before = len(chunk)
            #                     _mask = ~condition
            #                     logging.warning(f"Excluded {_mask.sum()} rows: <reason>")
            #                     chunk = chunk[condition]
            #                     rejected_by_reason['<rule_name>'] = \
            #                         rejected_by_reason.get('<rule_name>', 0) + (_before - len(chunk))
            #   DEFAULT_VALUE:    chunk[col] = chunk[col].where(condition, other=default)
            #                     # does NOT remove rows → no rejected_by_reason entry
            #   FLAG_AS_SUSPICIOUS: chunk['is_suspicious'] = ~condition
            #                       # Do NOT filter after flagging — keep all rows
            #                       # does NOT remove rows → no rejected_by_reason entry

            # Do NOT keep an in-loop `rejected_rows += ...` counter — the scalar total is
            # DERIVED after the loop as sum(rejected_by_reason.values()) (see below), so the
            # two can never drift out of sync.

            # 3c. Type casting — cast float64 → Int64 for integer/count/quantity columns
            int_cols = [c for c in chunk.select_dtypes(include='float64').columns
                        if any(kw in c.lower() for kw in ['quantity', 'qty', 'count', 'units'])]
            for col in int_cols:
                chunk[col] = chunk[col].astype('Int64')

            # 3d. Write — storage_options is MANDATORY, do not omit it. Use dict() (NOT {})
            # so the empty-dict literal has no braces to accidentally double-brace into {{}}.
            chunk.to_parquet(
                f"{partition_uri}part_{i}.parquet",
                engine="pyarrow",
                compression="snappy",
                index=False,
                storage_options=dict()
            )
            logging.info(f"Chunk {i}: {len(chunk)} rows processed")
            total_rows += len(chunk)

    except Exception as e:
        logging.error(f"Pipeline failed: {e}")
        raise

    # Scalar total DERIVED from the per-reason dict — single source of truth, so the
    # Rejection Rate panel (which uses rejected_rows) and the Rejections-by-Reason panel
    # (which uses the dict) can never disagree.
    rejected_rows = sum(rejected_by_reason.values())
    duration_seconds = time.time() - start_time   # for pipeline_duration_seconds metric
    logging.info(f"Pipeline completed. Rows: {total_rows}, rejected: {rejected_rows}, duration: {duration_seconds:.1f}s")

    # ── 4. TRINO PARTITION REGISTRATION ──────────────────────────────────────
    trino_host = os.getenv("TRINO_HOST", "trino.analytics.svc.cluster.local")
    conn = trino_connect(host=trino_host, port=8080, user="pipeline")
    cursor = conn.cursor()
    # Fill the bare schema and table names as STRING LITERALS directly in the CALL below —
    # exactly as the catalog "hive" is a literal. Do NOT assign `schema`/`table` (or `catalog`)
    # variables: the model tends to fill the real value into BOTH the assignment AND the string,
    # leaving the variable unused (ruff F841) and the f-string placeholder-less (ruff F541). Use
    # a PLAIN string (no f-prefix) with the literals inlined.
    # sync_partition_metadata takes EXACTLY THREE args: ('<schema>', '<table>', 'ADD'). The
    # catalog `hive` lives ONLY in the `hive.system.` prefix — NEVER inside the args, in either
    # of these two wrong shapes:
    #   ❌ CALL hive.system.sync_partition_metadata('marketing_global', 'orders', 'ADD')
    #        → Trino looks for a schema named 'hive.marketing_global' → "Table ... not found"
    #   ❌ CALL hive.system.sync_partition_metadata('marketing_global', 'orders', 'ADD')
    #        → 4 args: the mode 'ADD' is cast to the boolean case_sensitive param →
    #          "Cannot cast type varchar(3) to boolean"
    #   ✅ CALL hive.system.sync_partition_metadata('marketing_global', 'orders', 'ADD')
    # The schema is the BARE middle segment of `hive.<schema>.<table>`. If the objective shows the
    # target fully-qualified, use only the middle segment — and never add `hive` as its own arg.
    # Retry the partition registration. A freshly-started Trino coordinator (the init container
    # creates this table only seconds before the pipeline runs) may not yet see it in its Glue
    # catalog and raises "Table ... not found" TRANSIENTLY — the table IS in Glue and becomes
    # visible within a few seconds. Retry rather than crash: a crash here aborts BEFORE the
    # metrics emission below, leaving EVERY Grafana panel empty for an otherwise-successful run.
    for _attempt in range(5):
        try:
            cursor.execute("CALL hive.system.sync_partition_metadata('crm_us', 'pipe_move_data_post_progress_to_adl_to_azure', 'ADD')")
            cursor.fetchall()
            break
        except Exception as _e:
            if "not found" in str(_e).lower() and _attempt < 4:
                logging.warning(f"Trino table not visible yet (attempt {_attempt + 1}/5) — retrying in 3s.")
                time.sleep(3)
                continue
            raise
    logging.info(f"Trino partition run_date={run_date} registered.")

    # ── 5. METRICS EMISSION ───────────────────────────────────────────────────
    pushgateway_url = os.getenv("PUSHGATEWAY_URL", "http://pushgateway.monitoring.svc.cluster.local:9091")
    project_id     = os.getenv("PROJECT_ID", "unknown")
    cloud_provider = os.getenv("CLOUD_PROVIDER", "unknown")

    # Emit ALL FIVE metrics — the Grafana dashboard renders one panel per metric
    # (volume, freshness, data quality, performance, per-reason breakdown).
    # Omitting any leaves a panel with "No data".
    registry = CollectorRegistry()
    Gauge('pipeline_rows_processed_total', 'Total rows written to storage after business rules',
          ['project_id', 'cloud_provider'], registry=registry) \
        .labels(project_id=project_id, cloud_provider=cloud_provider).set(total_rows)
    Gauge('pipeline_last_success_timestamp', 'Unix timestamp of last successful run',
          ['project_id', 'cloud_provider'], registry=registry) \
        .labels(project_id=project_id, cloud_provider=cloud_provider).set(time.time())
    Gauge('pipeline_rows_rejected_total', 'Rows removed by DROP_RECORD / EXCLUDE_AND_LOG rules',
          ['project_id', 'cloud_provider'], registry=registry) \
        .labels(project_id=project_id, cloud_provider=cloud_provider).set(rejected_rows)
    Gauge('pipeline_duration_seconds', 'Wall-clock duration of the extract-transform-write phase',
          ['project_id', 'cloud_provider'], registry=registry) \
        .labels(project_id=project_id, cloud_provider=cloud_provider).set(duration_seconds)

    # Per-rule breakdown — one series per business rule that removed rows.
    # `reason` is the quality_standards rule name (never hardcoded). A pipeline with no
    # DROP_RECORD / EXCLUDE_AND_LOG rules emits zero series here (panel shows "No data").
    rejected_by_reason_gauge = Gauge(
        'pipeline_rows_rejected_by_reason', 'Rows rejected per business rule, labelled by rule name',
        ['project_id', 'cloud_provider', 'reason'], registry=registry)
    for _reason, _count in rejected_by_reason.items():
        rejected_by_reason_gauge.labels(
            project_id=project_id, cloud_provider=cloud_provider, reason=_reason).set(_count)

    push_to_gateway(pushgateway_url, job=project_id, registry=registry)
    logging.info(
        f"Metrics pushed: rows={total_rows}, rejected={rejected_rows}, "
        f"by_reason={rejected_by_reason}, duration={duration_seconds:.1f}s, cloud={cloud_provider}"
    )


if __name__ == "__main__":
    run()