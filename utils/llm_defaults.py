"""Shared LLM defaults for the NL / demo surface.

The AGENT pipeline reads its model from `agents/llm_factory.get_llm` (the `LLM_MODEL` env var).
The natural-language authoring / demo surface (`nlp_parser`, `rules_loader`,
`architecture_advisor`) uses a deliberately small, cheap model for constrained JSON extraction —
kept on its OWN knob so the agent and the demo can be tuned independently (e.g. agent on gpt-4o,
extraction on mini). Override with the `NL_MODEL` env var.

Temperatures are intentionally NOT centralised here: they differ by purpose (0 for deterministic
extraction, 0.1 for rule extraction, 0.2 for the architecture advisor) and stay at each call site.
"""
import os

NL_MODEL = os.getenv("NL_MODEL", "gpt-4o-mini")
