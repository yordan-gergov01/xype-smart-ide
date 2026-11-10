# Open questions for Phase 0 review

Answers received on 2026-11-07 are recorded inline. Items under **Open** still need an answer before the phase named.

## Answered

| # | Question | Answer | Consequence |
|---|---|---|---|
| Q1 | Company OS interface | **REST** (between the two systems) | `CompanyOSClient` gets an `httpx` REST adapter; fake with fixtures until the API spec is shared (Q1a). |
| Q2 | Template repo paths | **Not ready yet**, added later | Template registry and template evals use seeded sample repos until then; the template list stays config-driven. |
| Q3 | `master` vs `main` | **`master`** | Rules, CI and release config use `master` as the default branch. |
| Q5 | Local hardware | **CPU only, 8 GB RAM** | Local memory budget ≤ 4 GB for compose (scale-estimates §6); embedder served by Ollama locally; fork built on CI; 7B models out of scope locally. |
| Q6 | GitHub | Account **`yordan-gergov01`**, repo **`xype-smart-ide`** | Remote `yordan-gergov01/xype-smart-ide`; CI and release-please target it. |
| Q8 | ADR-0004 quorum queues | **Approved** | ADR-0004 accepted. |
| Q14 | Currency | **USD everywhere** | Prices, cost ledger and budgets in USD (`budget_usd_month`); no FX conversion. |
| Q15 | Extra task states | **Approved** | `DEFERRED` and `WAITING_FOR_CLIENT` stay in schema and proto. |
| Q6a | Repo visibility | **Public** | Secrets hygiene is critical: secret scanning in pre-commit and CI from the first commit; no client code in fixtures. |
| Q9 | Paid LLM route | **OpenRouter** (if possible) | ADR-0005 rewritten: one OpenAI-compatible adapter, OpenRouter profile with ZDR + provider allow-list. |
| Q10 | Embedder dims | **1024** | First migration uses `vector(1024)`. |
| Q12 | Matt Pocock skills | **Vendored** after review, per the approved Phase 1 plan; vendored in PR #1 on 2026-11-08 (no `npx` download) | Copies pinned to one upstream commit, with the MIT notice in `.claude/skills/THIRD_PARTY_NOTICES.md`. |
| Q16 | Git identity | `user.name = yordan-gergov01`, `user.email = yordansoftskills@gmail.com` | Set in this repo's local git config. |

## Open

| # | Question | Why it matters | Blocking for |
|---|---|---|---|
| Q1a | Company OS REST API: base URL, auth (API key / OAuth client), and an OpenAPI spec or example responses for project, client and team? | Real adapter and contract fixtures. | Phase 6 |
| Q7 | Jira Cloud or Data Center? Project key(s) and transition names for "In progress" / "In review"? | Jira adapter and the "Implement ticket" flow. | Phase 6 |
| Q11 | Auth: client credentials created per developer by an admin in the admin dashboard? Any SSO provider planned (Google Workspace, Entra ID)? | Gateway auth, OIDC readiness. | Phase 2 |
| Q13 | Brand assets: XYPE logo/icon files (SVG), or take them from xype.io? | Design tokens (Phase 4), branding (Phase 8). | Phase 4 |
