---
paths:
  - "server/prompts/**"
  - "server/backend/services/orchestrator/**"
  - "server/llm/**"
  - "server/evals/**"
---

# 10 — LLM prompts

- Do keep prompt templates as **versioned files** in `server/prompts/<template_id>/v<N>.*` (never in the database). Use the `add-prompt-template` skill.
- Do bump the version for any change that alters model input; never edit a released version in place.
- Do order every prompt for provider prefix caching: **stable prefix first** (shared rules pack, repo rules pack `.xype/rules.md`, template summaries, tool definitions — byte-identical across requests), **volatile suffix last** (query, retrieved chunks, cursor context).
- Never put timestamps, request ids or other per-request values in the stable prefix.
- Do include: structured instructions with JSON-schema output for edits (`explanation`, `code_diff`); good and bad examples; negative instructions (e.g. "Do not add DB migrations", "Do not invent API names"); the rule "prefer existing template patterns over inventing new ones".
- Do add model-specific variants only when an eval shows they help.
- Every new or changed template needs eval cases (`add-eval-case` skill) and must not regress the eval baseline.
- Do include the model id, model version and prompt version in every cache key.
- Do validate model output (JSON schema, syntax check, anomaly flags) before returning it.
