# Glossary

Domain language for XYPE IDE. Use these terms in code, specs, tickets and commits. Definitions say what a thing **is**, not how it is built. Maintained with the `domain-modeling` skill.

## Product

**XYPE IDE**: the agency's AI-native editor: a Code-OSS fork plus the `xype-ai` extension and its server.
_Avoid_: the app, the tool

**Workspace**: the set of folders a developer has open in one IDE window.

**Repository**: a registered XYPE git repository the AI may index and work on.
_Avoid_: project (means something else here), codebase (when one repo is meant)

**Template**: a registered repository that holds an XYPE framework pattern (FastAPI, React+TS, deploy, …) used as reference and as the source of new projects.
_Avoid_: boilerplate, starter

**Project**: an XYPE client or internal engagement, as known in Company OS; owns repositories and budgets.

**Team**: a group of developers sharing caches, repositories and budgets.

**Rules pack**: the org-level plus repo-level (`.xype/rules.md`) conventions injected into every prompt.

## Context engine

**Chunk**: an AST-delimited piece of code (function, class, block) and the unit of indexing and retrieval.
_Avoid_: snippet (reserved for the code around the cursor), fragment

**Obfuscated id**: a keyed hash that stands in for a file path or symbol name outside the client.

**Merkle tree**: the hash tree over a repository's files and chunks used to find what changed.

**Delta sync**: re-indexing only the chunks whose Merkle hashes differ between client and server.

**Selective fetch**: the server asking the client, over the open session stream, for the exact chunks it needs.

**Scrubbing**: replacing secrets and personal data with `<SECRET_REMOVED>` on the client before encryption.

**Session stream**: the persistent bidirectional gRPC connection between one IDE window and the server.

## AI features

**Completion**: an inline code suggestion at the cursor, including fill-in-the-middle; the synchronous path.

**Chat**: a question answered with codebase context, scoped to files or the whole codebase; streamed.

**Agent task**: a long-running background job (tests, refactor, docs, implement a ticket) that runs tools on the client.

**Client tool**: an action an agent task asks the client to perform (read, search, propose edit, run tests/commands), subject to approval rules.

**Backup model**: a faster, cheaper model used when the primary is slow, failing or over budget; its answers are labelled as such.

**Off-peak window**: the hours when a provider's prices are lower; non-urgent work may be deferred into them.

## Cost

**Budget**: the monthly USD limit per user or project for paid model usage.

**Hard cap**: the monthly limit above which every paid call is blocked.

**Cost ledger**: the record of every model call's tokens and USD cost.

## Code structure

**Feature**: one brief module (retrieval, cache, router, …) as it appears in the server: a package per layer under `backend/api`, `backend/services` and `backend/dal` (ADR-0006).
_Avoid_: package (means a Python distribution here)

**Layer**: one of `api`/`grpc` (transport), `services` (business rules) and `dal` (data access); imports point only downwards.

**Correlation id**: the id that follows one request across REST, gRPC, RabbitMQ and workers (`x-correlation-id`); it appears in every log line and span of that request.

**Readiness**: whether a service can reach every dependency it needs right now (`/readyz`); liveness (`/healthz`) only says the process runs.

**Schema owner**: the database role that owns the schema; only the one-shot migrate service uses it.

**Runtime role**: the database role the backend connects with; it reads and writes rows but cannot change the schema.

**Edge proxy**: the only container with a published API port; it forwards to the backend, which has no route outside the stack.
