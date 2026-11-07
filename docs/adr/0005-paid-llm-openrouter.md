# 0005 — Paid LLM access through OpenRouter (OpenAI-compatible adapter)

- Status: Accepted (direction chosen by the project owner on 2026-11-07: "OpenRouter if possible"); provider details re-verified in Phase 10
- Date: 2026-11-07
- Brief reference: §0 Cost rule, §1 Paid LLM, §5

## Context and problem statement

The paid adapter is built now, contract-tested against recorded fixtures only, and enabled in Phase 10 by configuration after explicit approval. The owner prefers OpenRouter over a direct DeepSeek account. Facts checked on 2026-11-07:

- OpenRouter exposes an OpenAI-compatible API and lists **DeepSeek V4 Pro** (`deepseek/deepseek-v4-pro`). The model page showed ~13 third-party hosting providers (e.g. StreamLake, SiliconFlow, DeepInfra, Nebius, Azure) with input $0.82–1.91 and output $1.50–3.83 per 1M tokens. DeepSeek itself was not visible in that list.
- OpenRouter states it adds **no markup on inference**; a fee applies when buying credits. BYOK (own provider key) is free up to $25k/month, 5% above that.
- **Zero Data Retention** can be enforced account-wide or per request with `provider.zdr: true`; implicit prompt caching is compatible with ZDR.
- DeepSeek prompt caching is automatic; cache reads cost 0.1× input. Usage returns `prompt_tokens_details.cached_tokens`. OpenRouter keeps requests "sticky" to one provider (or by `session_id`) to keep the cache warm; a manual `provider.order` disables stickiness.
- **No off-peak pricing** was shown on OpenRouter. The 50% off-peak discount exists only on DeepSeek's own API (01:00–04:00 and 06:00–10:00 UTC weekdays are peak).
- Direct DeepSeek API (`https://api.deepseek.com`) is OpenAI-compatible too, with IDs `deepseek-v4-pro` and `deepseek-flash`.

## Decision drivers

- One account and one key for many models (backup chains, alternatives, evals across models).
- Privacy: zero retention (brief §3.1, §3.7).
- Cost: prefix caching, budget control.
- Keep adapters minimal.

## Considered options

1. **OpenRouter** through one OpenAI-compatible adapter, with ZDR and a provider allow-list.
2. Direct DeepSeek API (OpenAI-compatible).
3. Direct DeepSeek Anthropic-compatible endpoint (needs `claude*` model aliases, ignores `cache_control`).

## Decision outcome

Chosen option: **1, OpenRouter**, implemented as a single `OpenAICompatibleProvider` adapter (`httpx`, SSE streaming) with **profiles**:

| Profile | Base URL | Model IDs | Use |
|---|---|---|---|
| `openrouter` (default paid) | `https://openrouter.ai/api/v1` | `deepseek/deepseek-v4-pro`, Flash ID verified in Phase 10 | primary |
| `deepseek` (optional) | `https://api.deepseek.com` | `deepseek-v4-pro`, `deepseek-flash` | only if off-peak savings justify a second account |

Request policy for every OpenRouter call:
- `provider.zdr: true` (and ZDR enabled in the account settings as a second guard).
- `provider.only` = an approved provider allow-list kept in config (data residency and quality are reviewed per provider in Phase 10); `allow_fallbacks` within that list only.
- `session_id` = the chat session / agent task id, so OpenRouter's sticky routing keeps the provider prefix cache warm (no manual `provider.order`, which would disable stickiness).
- Usage (`prompt_tokens`, `cached_tokens`, `completion_tokens`) and the provider name that served the call go to `cost_ledger` and metrics.

Enablement guard (unchanged): disabled unless `LLM_PRIMARY=openrouter:<model>`, `OPENROUTER_API_KEY` set, `PAID_MODELS_ENABLED=true`, and the monthly hard cap > 0. CI fails if any of these is set. Fixtures come from documented example payloads, never from live calls during development.

### Consequences

- Good: one adapter covers OpenRouter and direct DeepSeek (only the base URL and model IDs differ); easy backup chains and multi-model evals.
- Good: ZDR enforced at the gateway level.
- Bad: **the off-peak lever (brief §5, `CostScheduler`) does not lower prices on OpenRouter** as checked today. `CostScheduler` stays (it also smooths load), but its savings only apply with the `deepseek` profile. Phase 10 compares: OpenRouter third-party prices vs. DeepSeek direct off-peak.
- Bad: third-party hosts may serve different checkpoints/quantizations; the provider allow-list and the Phase 10 eval run per provider mitigate this.
- Bad: requests pass through an extra intermediary; covered by ZDR and TLS.

### Confirmation

- Contract tests: `OpenAICompatibleProvider` (both profiles) and the mock provider pass the same `LLMProvider` suite on fixtures.
- A test asserts every OpenRouter request body contains `provider.zdr = true` and a non-empty `provider.only`.
- Phase 10: re-check model IDs, providers, prices and ZDR availability before enabling.

## More information

- https://openrouter.ai/deepseek/deepseek-v4-pro
- https://openrouter.ai/docs/features/zdr
- https://openrouter.ai/docs/features/prompt-caching
- https://openrouter.ai/docs/faq
- https://api-docs.deepseek.com/quick_start/pricing
