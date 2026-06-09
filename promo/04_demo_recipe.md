# Promo Demo — Recipe (the exact live NL→deploy take)

The goal of this file: make the **live NL build** in the hero (CLIP A → CLIP D) succeed with
confidence. Everything here is grounded in the REAL bootstrap + the REAL seeded source table.

## TL;DR
| Choice | Value | Why |
|---|---|---|
| **Cloud to bootstrap** | **AWS** | The validated `eu_sales` baseline already provisions the source Postgres RDS + the table the demo reads. Reuse it. |
| **Source table** | **`raw_eu_sales`** | The ONLY real table on the sales RDS (`scripts/seed_chaos.py`). Columns: `order_id, unit_price, quantity, order_date, currency`. |
| **Demo slug** | **`promo_orders`** | Distinct → never overwrites `eu_sales` configs; fresh S3 bucket `promo-orders-insights-data` + IAM role. |
| **Rules file to load** | **`promo/demo_rules.yaml`** | Explicit, real columns → deterministic extraction + real rejections on the dashboard. |
| **Result B-roll** | **`eu_sales` Grafana** (pre-run) | Don't wait for the new pipeline to land on the cluster live — cut to the pre-run dashboard. |

> ⚠️ **Do NOT use the description in `01_caption_script_hero.md` verbatim** — it names `orders` /
> `total_amount` / `customer_email`, which **do not exist** on `raw_eu_sales`. The Architect would
> build rules on missing columns → `KeyError` at runtime. Use the description below + update that
> caption line to match (see "Caption fix").

---

## The EXACT NL description to type (CLIP A)

```
Take the raw_eu_sales orders table from my Postgres database to AWS S3, run it daily.
Drop any rows where unit_price is zero or negative, and flag any rows with negative quantity.
```

What the extractor (`_extract_intent`, gpt-4o-mini) will produce:
- `target_cloud: aws` ("AWS S3"), `source_db_type: postgres`, `data_domain: sales`
- `source_table: raw_eu_sales` ← **real table, exists after seeding**
- `frequency: daily`
- 2 rules: drop `unit_price <= 0` (DROP_RECORD), flag negative `quantity` (FLAG_AS_SUSPICIOUS)

In the wizard: set **pipeline name → `promo_orders`**, confirm **cloud → aws**. Then in Step 2,
do the "load your own rules" beat → pick **`promo/demo_rules.yaml`** (5 rules, all real columns).
Those 5 rules map 1:1 to the chaos the seeder injects, so every panel populates (incl. the
"Rejections by reason" piechart).

---

## Pre-production checklist (do BEFORE recording)

Run from the repo root with `.env` loaded (`python-dotenv`). `.env` must have: `OPENAI_API_KEY`,
`PINECONE_API_KEY`, `GITHUB_TOKEN` (= the classic PAT), `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`.

1. **AWS bootstrap live.** If not already applied: `bootstrap/aws/` applied. Then export:
   ```
   python scripts/export_bootstrap_outputs.py        # writes .bootstrap_outputs.json
   ```
   (Without this, the NL build shows `REQUIRES_BOOTSTRAP_OUTPUTS` sentinels — fail-loud.)

2. **Start the source RDS** (it's stopped to save cost):
   ```
   aws rds start-db-instance --db-instance-identifier eu-sales-raw-data
   aws rds wait db-instance-available --db-instance-identifier eu-sales-raw-data
   ```

3. **Seed the chaos data** so the table exists with anomalies (drops/flags will be visible):
   ```
   python scripts/seed_chaos.py --target eu_sales --db-type postgres --rows 100
   ```
   This (re)creates `raw_eu_sales` (100 rows: bad unit_price/quantity/currency/future dates).

4. **Pre-run a real pipeline for the RESULT shot** (so CLIP F has a populated dashboard — don't
   wait ~15–30 min live). Run the validated `eu_sales` end-to-end once, note its Grafana
   LoadBalancer URL. If `run_date=<today>` already exists in S3 the run idempotency-skips before
   emitting metrics — delete just today's partition first:
   ```
   aws s3 rm s3://eu-sales-insights-data/processed/run_date=$(date +%F)/ --recursive
   ```

5. **Capture the 4-cloud montage (CLIP G)** — screenshots/short clips of AWS Grafana, Azure
   Grafana, GCP Grafana, Databricks Lakeview from the validated runs (a torn-down cloud → a clean
   screenshot is fine).

6. **Launch Streamlit** (dark theme + no Deploy button already in `.streamlit/config.toml`):
   ```
   .venv/bin/streamlit run streamlit_app.py --server.headless true
   ```
   Confirm on screen: dark theme, NO Streamlit "Deploy" button, NO "Bootstrap not exported" card,
   the "💬 Natural Language" tab is the landing tab.

7. **Confirm the cost numbers** before recording Scene 3: `python utils/cost_estimator.py`
   (the captions quote AWS $279 · Azure $182 · GCP $162 · Databricks $119 ✓ — re-confirm).

---

## What "Confirm & Deploy" actually does (so nothing surprises you on camera)
The Streamlit deploy runs the **live agent graph** (`graph.stream`) at PIPELINE level on the
existing AWS bootstrap — Architect generates artifacts → Infra runs terraform (new
`promo-orders-insights-data` bucket + IAM) → pushes to GitHub. It is **slow** (~15–30 min if you
let CI fully land on the cluster). For the hero you only need the visually interesting stretch —
the graph nodes lighting up + the "✅ … complete" / "✅ Pushed to GitHub" beats. You can stop
CLIP D there and cut to the pre-run `eu_sales` dashboard (CLIP F). You do NOT need the new
`promo_orders` pipeline to finish landing on the cluster for the video.

The credentials path is identical to the validated `eu_sales` run: the generated db config uses
`POSTGRES_DB_*` env-var names, which `cloud_get("aws", …)` resolves via SSM (the same params
`eu_sales` uses) — so `read_data_schema` introspects the real `raw_eu_sales` and the agents run on
proven rails.

---

## Caption fix (coherence with `01_caption_script_hero.md`)
Scene 1 / CLIP A currently quotes the non-existent-column description. Update those two spots to
the real description above (keeps the video honest — the typed text matches the live schema). The
captions themselves (1–14) need no change; only the *typed description* and the CLIP A "Description
to type" line.

---

## After the shoot — cost
- `aws rds stop-db-instance --db-instance-identifier eu-sales-raw-data`
- The `promo_orders` deploy created a real S3 bucket + IAM + (if it landed) K8s workloads — tear
  the workloads down with `cleanup_k8s.yml`; the bucket/IAM can be removed via its terraform or
  left (negligible). It never touches the bootstrap infra.
