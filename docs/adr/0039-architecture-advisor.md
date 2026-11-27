# 0039 — Architecture advisor with a decision knowledge base and a web research tool

- Status: Proposed (deviation from brief §4 point 5, rule 03 and `CLAUDE.md`: retrieval outside registered repositories and templates, and web search in v1; needs the owner's approval)
- Date: 2026-11-27
- Deciders: project owner
- Brief reference: §3.4 (chat, agents), §4 points 2 and 5 (rules packs, scope), §5 (cost)

## Context and problem statement

Agents are good at writing and changing code, and weak at decisions with trade-offs: which store, which consistency model, a queue or a synchronous call, one service or several. The owner wants XYPE to give a very good architectural recommendation for the concrete situation, based on sound engineering knowledge and on research such as Stack Overflow answers.

Two constraints shape the design. First, the knowledge must be lawful to use: copyrighted books may not be copied into the index, the product or the public repository, whatever the copy's source and whether or not it is named. The ideas in them are general engineering knowledge, and the text is not ours. Second, a web search sends a query to a third party, so it must never carry code, paths, symbols or secrets (rule 07).

## Decision drivers

- Decisions the user can check: options, drivers, trade-offs and when to revisit, not a bare answer.
- Grounded in the repository's own context: its templates, ADRs, rules packs and code.
- Only knowledge we may lawfully use, with each source's licence recorded.
- No plaintext code or identifiers to any outside service, and fetched pages never treated as instructions.
- Cost bounded: no paid call without the Phase 10 approval and a budget.

## Considered options

1. **An advisor skill for chat and agents, grounded in our own decision knowledge base and the repository's context, with an opt-in web research tool.**
2. The advisor without web research: knowledge base and repository context only.
3. Index engineering books as found online (refused: copyright, and the repository is public).

## Decision outcome

Chosen option: **1**. Both new sources are outside the brief's scope rule (§4 point 5), so both are part of the deviation:

- the decision knowledge base is retrieved like a repository, but it is not one;
- the web research tool is general web search, limited to an allow-list.

Once the owner approves, rule 03 and `CLAUDE.md` get the same amendment: "plus the decision knowledge base and, when enabled, the web research tool (ADR-0039)".

### The advisor

- **Where:** in chat when the user asks for a decision, and proactively in agent tasks when the planned change contains an architectural choice (a new store, queue, service boundary, protocol or dependency).
- **What it produces:** questions about what it cannot infer (load, data size, consistency needs, team, budget, compatibility), then 2–4 options with their drivers and trade-offs, one recommendation, and the conditions that would change it. The result can be saved as an ADR in MADR form (`docs/adr/` of the user's repository); an ADR that drew on web research keeps the source links and their attribution.
- **Grounding:** retrieval over the decision knowledge base, plus the repository's own ADRs, rules packs, templates and the code in scope (the existing retrieval and privacy rules apply).

### Decision knowledge base

- **Written by the project** in its own words: one entry per situation, with the signals that point to it, the options, their trade-offs and when each fits. It is versioned and reviewed like code, and it cites and quotes no book (rule 09).
- **Open-licensed sources** may be added when their licence allows this use: an index entry records the source, the licence and the attribution, and a licence that forbids derivatives or commercial use is not indexed.
- Never a copy whose licence we have not verified.
- **Deferred: a user's own library** (books the user owns, indexed only on their machine). Retrieval and embeddings run on the server today, so a local-only index needs its own design: a local index, and passages sent to the model only as approved client context under rule 07. It gets its own ADR if wanted.

### Web research tool

- **A client-executed agent tool** under `ApprovalPolicy`, off by default and enabled per team. Only the client knows the plaintext paths and symbols (the server sees HMAC ids), so the client runs every check, then shows the query for approval, then makes the call.
- **Queries** describe the problem abstractly (technologies, versions, the error class). Before the approval prompt, the client refuses a query that contains:
  - a path, file name, symbol or repository name from its own index;
  - anything the scrubber would remove.

  The model that writes the query has seen the code, so this check cannot be skipped.
- **Sources:** an allow-list of hosts (Stack Overflow and other Stack Exchange sites, official documentation).
- **Fetched content is data, never instructions:**
  - a separate model call with **no tools** summarises the fetched pages;
  - only that summary reaches the agent, fenced as untrusted, with the links;
  - every tool call that follows it in the task needs fresh user approval, with no auto-approve.
- **Licence:** results are shown with links and their licence's attribution (Stack Overflow content is CC BY-SA). Fetched content is used only for the session. It is not cached (exact or semantic), not persisted with the chat, and not added to the knowledge base, so ShareAlike never applies to our stores.
- **Logging:** metadata only (counts, sizes, latency, outcome), never the query or the content (rule 08).
- **Provider and cost:**
  - which search API to use (the Stack Exchange API or another provider) is decided before the slice;
  - the provider gets a real adapter, an in-memory fake and a shared contract test (rule 03), and tests stay behind the network guard;
  - a paid host goes into `tools/paid_api_hosts.txt` and waits for the Phase 10 approval, with a budget and a rate limit.

### Phasing

| Part | Phase |
|---|---|
| Decision knowledge base (first entries) and the advisor in chat | Phase 4 (chat) |
| Proactive advice in agent tasks | Phase 5 (agents) |
| Web research tool | Phase 5, after this ADR is approved |
| Decision-quality evals: fixed scenarios with expected options and a scoring rubric | Phase 7 |

A third part of the owner's idea, an agent in a sandbox that watches the chosen small model and improves its context and reasoning, comes later. Without weight changes it is prompt, retrieval and example tuning measured by the Phase 7 evals. With weight changes it is ADR-0037's weight-space programming, under that ADR's gates.

### Consequences

- Good: decisions come with their reasons, fit the repository, and can be recorded as ADRs.
- Good: the knowledge base is ours, lawful and reviewable, and an index entry always shows where knowledge came from.
- Bad: writing a good knowledge base is ongoing work, and it starts small.
- Bad: the web research tool sends abstract queries to a third party and brings untrusted text into the session. It adds a provider dependency, possibly a cost, and an extra approval step after each research call.

### Confirmation

- A client test that a query carrying an indexed path, file name, symbol or repository name, or a scrubbed secret, is refused before the approval prompt.
- A test that the summarising call has no tools, and that a tool call after fetched content waits for fresh approval.
- A test that fetched content reaches no cache, chat store or knowledge-base entry.
- Decision-quality evals in Phase 7, run on the mock and local models only until Phase 10.
- Each knowledge-base entry and indexed source records its licence, and a CI check refuses an entry without one.
