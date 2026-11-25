# 0037 — Fine-tuning arms: QLoRA and weight-space programming, gated on provider terms

- Status: Proposed (deviation from brief §1 and §7, and from the Phase 10 rule for paid model use; needs the owner's approval)
- Date: 2026-11-25
- Deciders: project owner
- Brief reference: §7 (fine-tuning experiment track), §1 (local LLMs), §0 and §5 (no paid API without approval)

## Context and problem statement

Brief §7 plans one way to improve the small local completion model (qwen2.5-coder 1.5B
base, ADR-0003): QLoRA on a free Colab GPU, measured with the project's completion
evals. The owner wants to try a second way, **weight-space programming**: a frontier
model reads a small model's weights and edits them directly, with no gradients. The
idea comes from an MIT CSAIL blog post (2 Nov 2026,
https://rikiyat.github.io/blog/weight-space-programming/). This ADR decides whether
that becomes an arm of the Phase 9 experiment, and under what conditions. It departs
from the brief in two ways:

- §1 and §7 name Colab, QLoRA and GGUF as the fine-tuning stack. This ADR adds a technique
  and a different runtime: a CPU sandbox plus a hosted editor.
- §0, §5 and `CLAUDE.md` allow paid model use only after the Phase 10 approval, and
  Phase 9 comes first.

What the post shows, and what it does not:

| | |
|---|---|
| Editor | A model from another vendor. Nothing yet shows that Claude models can do this. |
| Closest case | Qwen3.5-0.8B: an edit to 131,072 weights (0.016%) took MATH-500 from 7.9 to 48.1 and GSM8K from 13.8 to 67.1, with 50 forward passes in a CPU Python sandbox. The base model hit the token limit 93% of the time and the edited one 33%. The gain came from **removing a failure mode** (looping), not from adding knowledge. |
| Null result | Gemma 3 270M: MATH-500 went from 6.8 to between 7.0 and 7.7. |
| Authors' limits | An edit can remove an obstacle but not add a missing ability. The post calls it a concept, not a method. |
| Not reported | Any code benchmark, any comparison with SFT or LoRA, and the cost. The edited model has no stated licence. |

So "as good as a frontier model" is not supported by the evidence. What is plausible:
the arm fixes concrete failure modes of the completion model, if it has any.

## Decision drivers

- The experiment track must not block the product (§7).
- §7: never use a paid provider for training without checking its terms, and ask the owner first.
- No paid API call without the owner's approval (§0, ADR-0012).
- Only code that may leave the machine reaches a hosted model (§3.7).
- Numbers are honest and comparable: the same harness, held-out data, and every null result reported.

## Considered options

1. **QLoRA only** — the brief as written.
2. **Weight-space programming with a hosted frontier editor** (for example Opus driven from Claude Code).
3. **Weight-space programming with a local open-weight editor** — no terms question, but probably too weak: the post needed a frontier model.
4. **QLoRA and weight-space programming as compared arms**, with option 2 as the editor, gated as below.

## Decision outcome

Chosen option: **4**. The experiment keeps QLoRA as its required arm and adds
weight-space programming as a gated arm.

### Gates (all must hold before an editing session runs)

1. **Provider terms.** The Anthropic Commercial Terms (D.4(a)) forbid using the services
   "to build a competing product or service, including to train competing AI models".
   The Consumer Terms (§3, item 2), which cover Pro and Max plans in Claude Code, forbid
   developing competing products, including by developing or training AI
   or machine-learning models. An AI coding IDE with its own completion model
   may count as a competing product, and editing weights may count as training.
   This gate covers only that clause as it applies to editing a model's weights. Using
   Claude Code to develop this repository is out of scope here and is raised with the
   owner separately. (Both retrieved 2026-11-25: Commercial Terms effective 2025-06-17, Consumer Terms
   effective 2025-10-08.) **This ADR does not decide that question.** No session with a
   hosted editor runs until there is a recorded basis for it: written confirmation from
   the provider or a legal opinion. That basis, with the version of the terms it covers,
   goes into this ADR as a yes or no outcome, and the terms are checked again right before each session. If the
   answer is no, option 3 is the only editor left, and it is judged on the same evals.
2. **Owner approval of the spend**, recorded here with a fixed token or plan budget.
   This is a deliberate exception to "no paid API outside Phase 10" (§0, ADR-0012) for
   editing sessions only. It is not the Phase 10 approval. These sessions do not go
   through the server's provider seam, so the ADR-0012 guard does not see them; the
   budget is the control. No code in `finetune/` calls
   a provider API; if a script ever does, its host goes into `tools/paid_api_hosts.txt`,
   so the hook and the CI guard keep blocking it in tests and CI.
3. **A failure mode to remove.** Before any session, measure on the practice split how
   often the base model repeats itself, stops in the wrong place, or produces
   completions that do not parse. If no failure mode affects at least 10% of the practice
   samples, skip the arm and report why. The threshold is fixed before the measurement.
4. **The Phase 7 completion evals exist** (ADR-0030). This ADR builds no harness of its own.
5. **An isolated editor environment.** Claude Code is an agent with file and shell tools,
   and the practice code may carry prompt injection. A session runs in a dedicated
   container or VM that holds only the base weights, the practice split and the scoring
   tool. It has no repository checkout, no eval data, no `.env` files or other
   credentials, and no SSH or cloud keys. Its only network access is the provider's
   endpoint. The eval split is stored nowhere that environment can reach.

### How a session runs

- **Target:** the base model chosen in ADR-0033, expected to be the completion model of
  ADR-0003 (qwen2.5-coder 1.5B base, Apache-2.0).
- **Data:** the editor reads the samples, so they are FIM samples from two sources only.
  One is the project's own seeded sample repositories. The other is public repositories
  whose licence permits this use; their licence checks stay in ADR-0033. Templates
  registered by users or teams are excluded, because they may be private. So is user
  code, and so is anything from the server (embeddings, Merkle data, logs). The ADR-0019
  scrubber runs over the practice split before a session, and its result is recorded.
  The **practice split** and the eval split are held out by repository. Public code may
  be in the base model's training data. Holding out by repository limits that but does
  not remove it, and the report says so.
- **Sandbox:** a CPU Python environment inside the gate-5 environment, with the weights, numpy and PyTorch. The
  editor runs the model only through a tool that scores the practice split. Each call
  uses up one forward pass. The **forward-pass budget** is fixed before the run, for
  example 10, 25 and 50 as in the post. Each budget is a separate session, reported
  on its own. No gradients are used, no training loop runs,
  and no search runs over the eval split.
- **Eval:** the editor never sees the eval split or its scores. The harness runs once,
  after the session ends, on four arms: base, QLoRA, weight-space, and QLoRA plus
  weight-space.
- **Report:** results per eval category for each arm, together with the fraction of weights
  changed, the passes used, the token cost and every null result. If an arm does not
  beat base, the report says so. The DeepSeek column that §7 asks for is added from
  Phase 10 on; this report does not replace it.
- **Artefacts:** no weights or weight deltas are committed: 131,072 fp32 weights are
  already about 0.5 MB. The repository keeps the edit script, the SHA-256 hashes of the
  base, delta and edited files, and the external store that holds them. The edited
  weights are an Apache-2.0 derivative of the base model, so they keep its LICENSE and
  NOTICE and state the modifications. The provider's terms on ownership of outputs are
  recorded with gate 1. The post's logs have no licence, so they serve as reference only
  and no code is copied from them. Export to GGUF for Ollama follows §7.

### Consequences

- Good: a cheap and fast arm if it works. A small edit needs no GPU, and the edited model
  serves at the same speed, because its architecture does not change.
- Good: QLoRA still gives §7 its answer whatever happens to this arm.
- Bad: the arm may never run, because of the terms gate. Even if it runs, it may show
  no gain on code.
- Bad: one more set of artefacts to keep reproducible and licensed.

### Confirmation

- The terms basis and the spend approval are recorded in this ADR before the first session.
- The isolation of the editor environment (gate 5) is checked and recorded before each session.
- The failure-mode measurement is in the Phase 9 report, run or skipped.
- The Phase 9 report has numbers for all four arms on the same held-out completion evals.

## Pros and cons of the options

### 1. QLoRA only

- Good: well understood, it fits the brief, and it raises no terms question.
- Bad: it needs a GPU (free Colab tier) and it leaves the owner's question unanswered.

### 2. Weight-space programming with a hosted editor, alone

- Good: the strongest editor.
- Bad: a terms risk, paid usage, and no baseline to say whether it helped more than QLoRA.

### 3. Local open-weight editor

- Good: free, private, and no terms question.
- Bad: likely below the capability the post relied on, so a null result would say little.

### 4. Compared arms, gated (chosen)

- Good: honest comparison. The gated arm cannot block the track.
- Bad: more work in Phase 9.

## More information

- Post logs: https://github.com/RikiyaT/lm_understand-logs (no licence stated).
- https://www.anthropic.com/legal/commercial-terms (D.4), https://www.anthropic.com/legal/consumer-terms (§3).
- Related: ADR-0003 (completion model), ADR-0012 (paid-API guard), ADR-0030 (eval gate, planned), ADR-0033 (base model and licence, planned).
