---
name: add-eval-case
description: Add an eval case to server/evals (code completion, chat explanation, agentic task, mutation, or negative-prompt) with a deterministic scorer, using only seeded or scrubbed XYPE data. Use when a prompt, model, router or agent behaviour changes, or a bug is fixed.
---

# Add eval case

1. Pick the suite: `completion` (removed function → exact/AST match/tests), `chat` (stripped docs → compare to original), `agentic` (task at a past commit → our tests + linters), `mutation` (injected bug → found and fixed), `negative` (risky request → flagged/refused/clarified).
2. Data: only seeded sample repos or snapshots of our repos/templates with secrets scrubbed. Never client secrets or personal data.
3. Write the case as data (`server/evals/datasets/<suite>/<case_id>.yaml`): input, expected outcome, scorer(s).
4. Prefer deterministic scorers: exact match, AST match, test pass, compiler-as-a-judge (syntax error = 0), refusal detector. Use an LLM judge only with a local model.
5. Run the suite on mock and local models only. If the baseline changes intentionally, update the versioned baseline file in the same commit and explain why.

## Checklist
- [ ] No secrets or client data
- [ ] Deterministic scorer
- [ ] Runs on free models only
- [ ] Baseline updated only with a stated reason
