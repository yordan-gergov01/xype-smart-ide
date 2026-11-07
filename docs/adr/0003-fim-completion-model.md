# 0003 — Local FIM completion model: qwen2.5-coder 1.5B on Ollama

- Status: Proposed
- Date: 2026-11-07
- Brief reference: §1 Local LLMs, §3.1 (P99 < 200 ms), §5 lever 5

## Context and problem statement

Code completion is served by a free local model that supports fill-in-the-middle, at P99 < 200 ms. It is also the base for the Phase 9 fine-tuning experiment, so its licence must allow fine-tuning and internal commercial use.

## Considered options

1. **qwen2.5-coder 1.5B (base)** — Apache-2.0, FIM-trained, Ollama tag `qwen2.5-coder:1.5b` (~1 GB).
2. qwen2.5-coder 3B — better quality, but licensed under **qwen-research**, which restricts commercial use. Excluded.
3. qwen2.5-coder 7B — Apache-2.0, better quality, ~4.7 GB; likely too slow on CPU for 200 ms.

## Decision outcome

Chosen option: **qwen2.5-coder 1.5B base**, through Ollama `/api/generate` with `prompt` + `suffix` (Ollama applies the model's FIM template), `keep_alive` long, small `num_predict`. 7B is a config switch (`LLM_COMPLETION=ollama:qwen2.5-coder:7b`) for GPU machines. The local chat model for e2e tests is chosen in Phase 1 (candidate: an Apache-2.0 instruct model of similar size).

### Consequences

- Good: free, fast enough to have a chance at 200 ms on CPU, permissive licence, good FIM support.
- Bad: quality ceiling of a 1.5B model; reported Ollama FIM quirks with instruct variants — use the base variant and verify the FIM template in Phase 3.

### Confirmation

Phase 3 latency benchmark (P50/P99) and the completion eval (exact / AST match) on our repos.

## More information

- https://huggingface.co/Qwen/Qwen2.5-Coder-1.5B (Apache-2.0)
- https://huggingface.co/Qwen/Qwen2.5-Coder-3B (qwen-research)
- Ollama API `suffix`: https://github.com/ollama/ollama/blob/main/docs/api.md
