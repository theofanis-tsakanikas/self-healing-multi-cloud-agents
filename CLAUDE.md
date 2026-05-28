# CLAUDE.md — Multi-Cloud Self-Healing Data Engineer Agent

## Project Identity

This is a **production-grade, cloud-agnostic AI orchestration system** that autonomously
designs, deploys, and self-heals data pipelines across AWS, Azure, and GCP.
Every decision must reflect that standard.

---

## Core Principles

### 1. Radical Honesty
- Never tell me what sounds good. Tell me what is true.
- If my approach is wrong, say so directly and explain why.
- If a task has a better solution than the one I described, propose it — don't just execute my instructions blindly.
- If something is incomplete or broken, say it clearly. No sugar-coating.

### 2. No Shortcuts. Ever.
- Always implement the production-grade solution, even if a simpler one exists.
- No `TODO` comments as substitutes for real implementation.
- No placeholder logic, mock functions, or "simplified for now" patterns.
- If the correct solution requires more time, say so — don't ship a shortcut.

### 3. Cloud Agnostic by Default
- This system targets **AWS, Azure, and GCP equally**. No cloud is the default.
- Never hardcode cloud-specific assumptions (e.g., assume S3 when the pipeline config may target ADLS or GCS).
- Cloud is always detected from `cloud_provider` in the pipeline/infra config — never guessed.
- Storage URIs, auth patterns, and SDK calls must always come from config, not assumptions.


## Git Workflow — Mandatory Protocol

After **every** set of changes, follow this exact sequence:

1. Run `git diff` and present the full output to the user.
2. Ask: **"Commit and push? [yes / no]"**
3. If **no**: Tell the user to make their edits in the editor, then say "Tell me when you're ready." Wait.
4. When the user confirms: run `git diff` again to show the final state.
5. Ask again: **"Commit and push? [yes / no]"**
6. If **yes**: commit with a conventional commit message and push.

### Commit Message Format (Conventional Commits)
```
<type>(<scope>): <short description>

Types: feat | fix | infra | docs | refactor | test | chore
Scope: architect | infra | medic | supervisor | bootstrap | configs | knowledge-base | ci

Examples:
feat(architect): add chunked parquet write with Int64 casting for GCP pipeline
infra(bootstrap): add GKE autopilot cluster with workload identity binding
fix(medic): correct partition URI format for Azure ADLS target
docs(knowledge-base): update terraform_gcp_bucket standard with GCS backend
```

**Never commit secrets, `.env` files, or credential JSON files.**

---

## Before You Suggest Any Change

Ask yourself:
- Is this cloud-agnostic? Would it work equally on AWS, Azure, and GCP?
- Is this the production solution or a shortcut?
- Am I about to commit a secret or hardcoded credential?

If any answer is no — stop and fix it first.