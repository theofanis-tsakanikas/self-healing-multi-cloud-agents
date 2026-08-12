# ADR-0001: Conventions are retrieved standards, not prompt folklore

- **Status:** Accepted
- **Date:** 2026-06 (recorded 2026-08)

## Context

An agent that generates Terraform, Kubernetes manifests, SQL DDL and CI workflows has to know a great
many conventions: how buckets are named, which state backend to use, what a Trino catalog file looks
like, which fields a Grafana dashboard needs. There are two places that knowledge can live.

The default is the prompt. It works until the prompt is four thousand words of accumulated
corrections, nobody remembers why half of them are there, and a fix for one cloud silently breaks
another. Worse, when output is wrong the temptation is to patch the *generated file* — which produces
a green run and no learning, because the next run regenerates the same mistake.

## Decision

Conventions live in `knowledge_base/` as **versioned engineering standards**, embedded into Pinecone
and retrieved by the agents at generation time. Prompts answer *what to do and when*; standards answer
*how exactly, and why*.

The rule that follows is absolute: **when the LLM produces wrong output, fix the standard or the
prompt — never the generated file.** A hardcoded one-off fix is never the answer, because the artifact
is an output, not source.

A corollary that costs people an hour if forgotten: Pinecone serves the *last synced* version, so
after editing any standard the next run must set `sync_knowledge_base: sync`, or the agents read the
old text and the edit is silently ignored.

## Alternatives rejected

- **Everything in the prompt.** Unversioned, unreviewable as a diff, and it grows without bound. It
  also cannot be selectively retrieved — every agent pays for every convention on every call.
- **Patching generated artifacts.** Produces a working demo and a system that has learned nothing.
  This is the anti-pattern the rule above exists to forbid.
- **Fine-tuning on the conventions.** A retraining cycle for a change that should be a pull request.

## Consequences

- A convention change is a reviewable diff in a Markdown file, and the next run picks it up.
- Standards are the specification for the code-owned generators too, so a rule change and the matching
  render change belong in the same commit ([ADR-0002](0002-the-llm-deterministic-boundary.md)).
- The corpus goes to a third-party SaaS, so it is audited to carry no credentials — stated as a
  limitation in `SECURITY.md`.
- Forgetting the re-sync is a real failure mode with no error message. It is documented in the prompt
  rules, in `CLAUDE.md`, and here.
