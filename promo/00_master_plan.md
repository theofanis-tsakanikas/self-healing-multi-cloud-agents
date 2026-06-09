# Promo Video — Master Plan

## Goal & audience
A portfolio / interview piece. The viewer (recruiter, CTO, engineer) must grasp in **~15 seconds**:
> *Describe a pipeline in plain English → AI agents build a production, multi-cloud pipeline → and it self-heals.*

Everything else in the video is **proof** of that one sentence.

## Two deliverables
| | Hero promo | Deep-dive |
|---|---|---|
| Length | **~90s – 2 min** | **~4–6 min** |
| Audio | Captions + light music, **no voiceover** | **Voiceover** + captions |
| Use | Lead with it / share on LinkedIn / top of portfolio | Linked "watch the full walkthrough" for technical viewers |
| Plan | this file + `01_caption_script_hero.md` + `02_shot_list.md` | `03_deep_dive_plan.md` |

## 4 principles
1. **Short + muted-friendly.** Autoplay-muted is the norm → it must tell the story **without sound**.
2. **Hero-first.** The "wow" in the first 10 seconds. No logo intro, no slow build-up.
3. **Show, don't explain.** Less text, more action. One punchy caption per beat.
4. **Honest.** Cut errors / waits / "Bootstrap not exported". Don't oversell. A staged self-heal must be a **real** fix.

## Audio decision
- **Hero = captions + subtle tech/ambient background music, NO voiceover.** Works muted, looks polished, zero audio-quality risk, fastest to produce, total control over pacing.
- **Captions:** large, dark + cyan (match the app theme), **one line per scene, in English** (international/interview audience).
- **Music:** modern / ambient / "tech", low energy, sits under the captions. (e.g. a calm electronic bed — no lyrics.)
- **Voiceover:** only on the deep-dive, where explaining the architecture adds value (and shows communication skill).

## Structure — Hero (~120s, 7 scenes)
| # | Time | On screen | Caption |
|---|------|-----------|---------|
| 0 | 0–8s   | Title card (dark); fast flash of 4 cloud logos + the agent graph | *Build a production data pipeline across 4 clouds — by describing it.* |
| 1 | 8–25s  | Zoom into NL box; type the description; Continue; AI extracts intent | *Describe it in plain English. The AI extracts the intent.* |
| 2 | 25–40s | Quick cuts: wizard fields auto-filled; load your own rules file | *It fills the gaps — or load your own rules.* |
| 3 | 40–62s | Step 3: execution plan ("what will be created") + 4-cloud cost comparison | *See the plan — and the cost on every cloud — before you deploy.* |
| 4 | 62–92s | Confirm & Deploy → agent graph lights up (Supervisor→Architect→Infra→Medic), logs, "Pushed to GitHub"; (optional) a CI failure → Medic fixes it | *Four agents design, deploy — and self-heal.* |
| 5 | 92–112s | Cut to the REAL deployed dashboard (live metrics + data); quick montage of the other 3 clouds' dashboards | *A real pipeline. Real data. Real observability.* |
| 6 | 112–120s | End card: project name + value prop + your name / GitHub | *Autonomous · self-healing · multi-cloud — AWS · Azure · GCP · Databricks* |

## Non-negotiables (the video must contain)
- The **NL → pipeline** magic
- **Multi-cloud (4)** — the cloud-agnostic thesis
- The **live agent orchestration** (the graph + logs)
- **Self-healing** — the differentiator (even if staged, a real fix)
- The **cost comparison** (unique, memorable)
- A **real result** — a deployed dashboard + data (proves it's not vaporware)

## Pre-production checklist (do BEFORE recording)
- [ ] **Bootstrap ONE cloud live** for the NL build. Recommended: **AWS** (`bootstrap/aws/`) → the NL demo builds an S3 pipeline → Grafana finale. Then `python scripts/export_bootstrap_outputs.py`.
- [ ] **Pre-run a real pipeline** so you have a **populated dashboard** to cut to (don't wait ~15–30 min live).
- [ ] **Capture the 4 cloud dashboards** (Grafana ×3 + Databricks Lakeview) for the scene-5 montage — short clips or screenshots from the validated runs.
- [ ] Use a **DISTINCT slug** for the NL demo (e.g. `promo_orders`) — a colliding slug overwrites existing configs.
- [ ] The NL description must point at a **REAL source table** (the Architect runs `read_data_schema` on it).
- [ ] Re-seed chaos data / delete today's partition so the run actually writes (idempotency skips an already-landed `run_date`).
- [ ] Streamlit running with the forced dark theme + Deploy button hidden (already configured in `.streamlit/config.toml`). Restart Streamlit after any config change.

## Honest do / don't
- **DO** speed-ramp the slow deploy (2–4×); keep the agent-graph beats watchable.
- **DO** cut to a pre-run dashboard for the result.
- **DON'T** show "Bootstrap not exported", tracebacks, or long waits.
- **DON'T** claim "replaces data engineers" → frame as *"encodes a senior engineer's playbook, and executes it reliably."*

## The one-line test
If a stranger watches the **first 15 seconds on mute** and can say *"you describe a pipeline and AI builds it across clouds and fixes itself"* — the cut works. If not, re-cut the opening.
