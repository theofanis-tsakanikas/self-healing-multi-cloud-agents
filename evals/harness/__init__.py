"""Offline replay + eval harness for the self-healing Medic.

The Medic's judgment has two layers, and this harness evaluates both without any cloud,
credentials, or spend:

* **Deterministic routing + the anti-hallucination evidence gate** — pure functions
  (`agents.medic._ci_error_owner`, `_extract_ci_failed_file`, `_owner_of_file`;
  `agents.tools.request_fix`). `evals.harness.deterministic` composes the *real* functions
  and `evals.harness.runner` scores them against `evals/corpus/corpus.json`, turning the
  previously eval-less whack-a-mole routing logic into a measured regression net. This is
  the **replay mode**: runnable by anyone (`make eval-replay`), no LLM key.

* **The LLM diagnosis/fix quality** — `evals.harness.eval_live` runs the whole `medic_node`
  with a real model (any provider, via `get_llm`) against mocked GitHub/terraform/time and a
  local knowledge-base retriever, and scores whether the model routes + grounds its fix
  correctly. This is the **eval mode**: needs an LLM key, catches model regressions.
"""
