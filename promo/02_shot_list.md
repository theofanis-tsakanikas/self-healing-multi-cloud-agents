# Hero Promo — Shot List (record this, in this order)

You record **clips**, then assemble them in the edit (clip order ≠ final scene order). Record the
slow/real things first so they're ready; the dashboard footage is reusable.

## Stage 0 — Setup (before any recording)
1. **Bootstrap AWS** (if not live): `bootstrap/aws/` applied → `python scripts/export_bootstrap_outputs.py`. (Sidebar should show "Cloud Bootstrap · AWS ready".)
2. **Pre-run a real pipeline** end-to-end once (e.g. `eu_sales`) so a Grafana dashboard is **populated** and reachable. Note its LoadBalancer URL.
3. **Re-seed chaos data** / delete today's `run_date` partition so the demo run actually writes.
4. Launch Streamlit: `.venv/bin/streamlit run streamlit_app.py --server.headless true`. Confirm: dark theme, **no** Streamlit "Deploy" button, **no** "Bootstrap not exported" card.
5. Screen Studio: 16:9, retina, hide desktop clutter, clean menu bar, browser at a comfortable zoom so captions don't fight the UI.

---

## Clips to record (in this recording order)

### CLIP F — Result dashboard (record FIRST, it's reusable B-roll)
- **What:** The populated **Grafana** dashboard of the pre-run pipeline — slow pan across the 5 panels; hover one for a tooltip.
- **Length:** ~15s raw (you'll use ~8s).
- **Screen Studio:** gentle auto-zoom on the panels; smooth cursor.

### CLIP G — 4-cloud dashboard montage (record / screenshot)
- **What:** The 4 dashboards: AWS Grafana, Azure Grafana, GCP Grafana, **Databricks Lakeview**. 2–3s each, or crisp screenshots.
- **Length:** ~12s raw (→ ~3–4s montage).
- **Note:** If a cloud is torn down, a clean screenshot from the validated run is fine.

### CLIP A — NL describe (the hero typing)
- **What:** "💬 Natural Language" tab. Type the description (steady, not too fast), click **Continue →**, let the "Extracting intent…" spinner play, land on Step 1.
- **Description to type:** `Take the orders table from my Postgres database to AWS S3, run daily, drop rows with negative total_amount, and flag missing customer_email.`
- **Length:** ~18s raw.
- **Screen Studio:** strong auto-zoom on the text box while typing; ease out to full wizard.

### CLIP B — Wizard fields + load rules
- **What:** Step 1 — scroll the auto-filled fields, open the **cloud** dropdown briefly (show AWS/Azure/GCP). Step 2 — show the 2 extracted rules, then **load a rules file** (file picker → "parsed N rules").
- **Length:** ~18s raw (→ ~12s, speed-ramped).

### CLIP C — Plan + cost
- **What:** Step 3 — pan the **Execution plan** card, then the **cost comparison** (4 cards). Click the breakdown **selector** to switch from Databricks to AWS and back (shows the per-line table).
- **Length:** ~20s raw.
- **Screen Studio:** zoom into the cost cards; a slow push-in reads great.

### CLIP D — Deploy + agent graph (the long real one)
- **What:** Click **✅ Confirm & Deploy**. Record the **whole** agent run — the graph nodes lighting up, logs streaming, each "✅ … complete", "✅ Pushed to GitHub". Don't stop early.
- **Length:** record the full run (could be minutes); you'll **speed-ramp 3–4×** and cut to the "✅" beats.
- **Screen Studio:** keep the graph + log panel in frame; you'll trim later.

### CLIP E — Self-heal (OPTIONAL money-shot)
- **What:** If you stage it: induce a real CI failure (or capture a real medic cycle) → the **Medic** node activates → logs "diagnosing / targeted fix" → a green re-run.
- **Length:** ~15s raw (→ ~6s).
- **Honesty:** only include if it's a REAL fix.

### Title + End cards
- Built in the editor (Screen Studio text scenes), not recorded. Text from `01_caption_script_hero.md` (scenes 0 and 6).

---

## Assembly order (in the editor) = final scenes
`Title → CLIP A → CLIP B → CLIP C → CLIP D (+ CLIP E) → CLIP F → CLIP G → End card`

Map to the script: 0 → 1 → 2 → 3 → 4 → 5 → 6.

---

## Screen Studio tips
- **Auto-zoom on clicks/typing** is your best friend — it focuses attention on the NL box, the cost cards, the "✅"s.
- **Speed-ramp** the deploy (CLIP D) hard (3–4×), but **slow back to 1×** on each "✅ complete" / "Pushed to GitHub" so they register.
- **Cursor smoothing + click highlights** on (defaults) — looks pro.
- Keep **one motion per beat** — don't zoom + pan + cut simultaneously.
- **Captions:** add as overlay text scenes; keep them lower-third so they never cover the action.
- **Music:** import a calm tech/ambient bed; duck it slightly under nothing (no VO), just keep it low; end on a clean resolve at the end card.
- Export **1080p (or 4K) MP4, ~30–60fps**; keep a captioned + a no-caption master if you might re-version.

## Final QC before you publish
- [ ] Reads on **mute** (the one-line test from `00_master_plan.md`).
- [ ] No errors / "Bootstrap not exported" / dead waits on screen.
- [ ] Under 2:00.
- [ ] The 4 clouds are visibly present.
- [ ] Ends with a clear "what is this + who made it" card.
