---
name: add-prompt-template
description: Add or change a versioned LLM prompt template in server/prompts with prefix-cache-friendly ordering and matching eval cases. Use for any change to what the model receives.
---

# Add prompt template

1. Location: `server/prompts/<template_id>/v<N>.md` (+ optional `v<N>.<model_family>.md` variant only when an eval shows it helps). Never edit a released version; create `v<N+1>`.
2. Structure, in this exact order:
   1. **Stable prefix** (byte-identical across requests): role, org rules pack, `.xype/rules.md`, template summaries, tool definitions, output JSON schema, good/bad examples, negative instructions ("Do not add DB migrations", "Do not invent API names", "Prefer existing XYPE template patterns").
   2. **Volatile suffix**: retrieved chunks, cursor context, the user's query.
   No timestamps or request ids in the prefix.
3. Declare variables and the output schema in the template front matter; `PromptRegistry` validates them.
4. Add eval cases (`add-eval-case` skill) covering the new behaviour and at least one negative case.
5. Run the eval suite on mock/local models and compare to the baseline; never regress it.
6. Note the prompt version in the commit; cache keys include it automatically.

## Checklist
- [ ] New version file, old version untouched
- [ ] Stable prefix first, volatile last, no per-request values in prefix
- [ ] JSON schema for structured outputs
- [ ] Eval cases added, baseline not regressed
