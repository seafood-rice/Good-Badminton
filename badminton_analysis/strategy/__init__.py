"""Match strategy report: a facts pack, one LLM call per language, number-checked claims.

Modules: ``config`` (settings), ``llm`` (OpenAI-compatible client), ``facts`` (the measured
facts pack), ``prompts`` and ``validate`` (what the model is asked and how its answer is
checked), ``report`` (generation, storage, Markdown). Spec:
``docs/superpowers/specs/2026-10-27-match-strategy-report-design.md``.
"""

SCHEMA_VERSION = 1
