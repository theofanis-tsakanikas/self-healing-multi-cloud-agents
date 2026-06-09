# Hero Promo — Caption Script (exact words + timings)

**Target:** ~120s · captions + music, no voiceover · captions in English.
**Demo values used below:** NL builds an **AWS / S3** pipeline; slug `promo_orders`; result = its **Grafana** dashboard; scene 5 montage shows the other 3 clouds.

Caption style: large, bottom-center or lower-third, white text with a subtle cyan accent on the
key word, dark semi-transparent backing. Keep each caption on screen 2.5–4s. One line, no walls of text.

---

### SCENE 0 — Hook (0:00–0:08)
- **Screen:** Black → the app title card fades in (dark gradient). A fast 1.5s flash montage: AWS / Azure / GCP / Databricks logos, then the 4-node agent graph (Supervisor → Architect → Infra → Medic).
- **Caption (0:02):** `Build a production data pipeline across 4 clouds —`
- **Caption (0:05):** `— by describing it.`
- **Music:** starts on the title card, soft swell.

### SCENE 1 — The ask (0:08–0:25)
- **Screen:** Streamlit, "💬 Natural Language" tab. Zoom into the description box. Type (or reveal pre-typed, then a cursor blink):
  > *Take the orders table from my Postgres database to AWS S3, run daily, drop rows with negative total_amount, and flag missing customer_email.*
  Click **Continue →**. Brief "Extracting intent…" spinner.
- **Caption (0:10):** `Describe it in plain English.`
- **Caption (0:19):** `The AI extracts the intent — source, destination, rules.`

### SCENE 2 — Guided + your rules (0:25–0:40)
- **Screen:** Fast cuts (speed-ramp). Step 1 fields auto-filled (zoom the cloud / DB dropdowns). Step 2 rules: show the 2 extracted rules, then **load a rules file** (`.yaml`) — a quick file-pick + "parsed N rules".
- **Caption (0:27):** `It fills the gaps for you…`
- **Caption (0:34):** `…or load your own business rules.`

### SCENE 3 — Plan + cost (0:40–0:62)
- **Screen:** Step 3 summary. Slow pan/zoom over: (a) the **Execution plan** card ("what will be created" — scripts, Terraform, K8s, dashboard, CI/CD), then (b) the **cost comparison** (4 cloud cards: AWS $279 · Azure $182 · GCP $162 · Databricks $119 ✓). Hover the breakdown selector to switch a cloud.
- **Caption (0:42):** `Before anything runs — see exactly what it will build…`
- **Caption (0:52):** `…and what it costs on every cloud.`

### SCENE 4 — Deploy + the agents (0:62–0:92)
- **Screen:** Click **✅ Confirm & Deploy**. The pipeline graph animates: Supervisor → Architect (logs stream: writing scripts, Trino DDL, dashboard) → "✅ Architect complete" → Infra (terraform, "✅ Pushed to GitHub — SHA …") → "✅ Infra complete". **Speed-ramp** the slow stretches; hold on each "✅".
  - **(Optional money-shot):** stage a CI failure → the **Medic** node lights up → logs "diagnosing… targeted fix" → re-run goes green.
- **Caption (0:64):** `Four AI agents take over.`
- **Caption (0:72):** `Design · deploy · push to GitHub · ship to the cloud.`
- **Caption (0:84, if self-heal shown):** `And when CI breaks — the Medic fixes it. Automatically.`

### SCENE 5 — The result (0:92–0:112)
- **Screen:** Cut to the **real Grafana dashboard** (the `promo_orders` run): 5 panels populated — records processed/rejected, rejection rate, run duration, rejections by reason. Then a 3–4s montage: Azure Grafana → GCP Grafana → **Databricks Lakeview** (the pretty one).
- **Caption (0:94):** `A real pipeline. Real data. Real observability.`
- **Caption (0:104):** `The same system — on AWS, Azure, GCP, and Databricks.`

### SCENE 6 — Close (0:112–0:120)
- **Screen:** End card (dark). Project name + the value line + your name / GitHub URL. Music resolves.
- **Caption (static):**
  > **Multi-Cloud Self-Healing Data Engineer Agent**
  > Autonomous · self-healing · multi-cloud
  > AWS · Azure · GCP · Databricks
  > *<your name> — github.com/<you>*

---

## Caption master list (copy-paste ready)
```
1.  Build a production data pipeline across 4 clouds —
2.  — by describing it.
3.  Describe it in plain English.
4.  The AI extracts the intent — source, destination, rules.
5.  It fills the gaps for you…
6.  …or load your own business rules.
7.  Before anything runs — see exactly what it will build…
8.  …and what it costs on every cloud.
9.  Four AI agents take over.
10. Design · deploy · push to GitHub · ship to the cloud.
11. And when CI breaks — the Medic fixes it. Automatically.
12. A real pipeline. Real data. Real observability.
13. The same system — on AWS, Azure, GCP, and Databricks.
14. [End card] Autonomous · self-healing · multi-cloud
```

## Notes
- If you SKIP the self-heal (caption 11), trim scene 4 to ~22s and give scene 5 the extra time.
- Keep total under 2:00. If tight, the fastest cut is scene 2 (rules) — it can drop to ~10s.
- Numbers in scene 3 come from `utils/cost_estimator.py` (run `python utils/cost_estimator.py` to confirm the current figures before recording).
