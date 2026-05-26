"""
Trino federation client.
Connects to a real Trino coordinator when TRINO_HOST is set;
falls back to demo mode with realistic sample data otherwise.
"""
from __future__ import annotations
import os
import pandas as pd

# ── Pre-built cross-cloud query shown in UI ─────────────────────────────────
CROSS_CLOUD_QUERY = """\
-- 🔮 Cross-Cloud Federation Query
-- Joins data from 3 separate cloud catalogs in a single SQL statement

SELECT
    s.customer_id,
    s.sale_amount,
    s.sale_date,
    m.campaign_name,
    m.campaign_spend,
    c.customer_tier,
    c.country
FROM
    hive.sales.transactions          s    -- ← AWS  (S3 + Hive metastore)
    JOIN gcp_catalog.marketing.campaigns  m    -- ← GCP  (GCS)
        ON s.customer_id = m.customer_id
    JOIN azure_catalog.crm.customers      c    -- ← Azure (ADLS Gen2)
        ON s.customer_id = c.customer_id
WHERE
    s.sale_date >= DATE '2026-05-01'
    AND c.customer_tier IN ('Gold', 'Platinum')
ORDER BY
    s.sale_amount DESC
LIMIT 100"""

# ── Demo data returned when Trino is not reachable ──────────────────────────
_DEMO_DF = pd.DataFrame({
    "customer_id":    [1001, 1002, 1003, 1004, 1005, 1006, 1007, 1008],
    "sale_amount":    [3200.00, 2750.50, 2100.00, 1980.75, 1650.00, 1420.25, 1310.00, 1050.50],
    "sale_date":      ["2026-05-06", "2026-05-05", "2026-05-06", "2026-05-04",
                       "2026-05-06", "2026-05-03", "2026-05-05", "2026-05-06"],
    "campaign_name":  ["spring_promo", "email_vip", "spring_promo", "referral",
                       "email_vip", "spring_promo", "retargeting", "email_vip"],
    "campaign_spend": [12000, 8500, 12000, 3200, 8500, 12000, 4100, 8500],
    "customer_tier":  ["Platinum", "Gold", "Platinum", "Gold",
                       "Gold", "Platinum", "Gold", "Gold"],
    "country":        ["DE", "FR", "NL", "IT", "ES", "DE", "BE", "AT"],
    "data_source":    ["AWS→GCP→Azure"] * 8,
})


def run_query(sql: str) -> tuple[pd.DataFrame, bool]:
    """
    Execute a Trino query. Returns (dataframe, is_live).
    is_live=False means demo mode — no real Trino connection.
    """
    host = os.getenv("TRINO_HOST", "")
    port = int(os.getenv("TRINO_PORT", "8080"))

    if host:
        try:
            import trino  # type: ignore
            conn = trino.dbapi.connect(
                host=host,
                port=port,
                user=os.getenv("TRINO_USER", "streamlit"),
                catalog="hive",
                schema="default",
            )
            cur = conn.cursor()
            cur.execute(sql)
            cols = [d[0] for d in cur.description]
            rows = cur.fetchmany(200)
            return pd.DataFrame(rows, columns=cols), True
        except Exception:
            pass

    return _DEMO_DF.copy(), False
