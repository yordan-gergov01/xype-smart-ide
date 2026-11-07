# 06 — Testing

## TDD in vertical slices
1. Agree on the seam and the public interface.
2. Write a failing test through the public interface.
3. Make it pass with the simplest code.
4. Refactor with tests green.

## What and how
- Do test behaviour through public interfaces, never private helpers or call order.
- Do prefer fakes (in-memory adapters) over mocks. Use mocks only at true external boundaries (HTTP to a third party).
- Do run one shared contract-test suite against both the real adapter and its fake.
- Do mark tests: unit (default, no I/O), `integration` (testcontainers: Postgres+pgvector, Redis, RabbitMQ), `e2e`, `load`.
- Never let a test call a paid API or the internet. The network guard fails any outbound connection except localhost and testcontainers.
- Never put real client code, secrets or personal data in fixtures. Use seeded sample repos.
- Coverage ≥ 85% on core modules; never lower a threshold to make CI pass.

## Bugs
- First build a reliable pass/fail reproduction (a failing test), then hypothesize (`diagnosing-bugs` skill).
- Every fixed bug gets a regression test in the same commit.

## Pyramid
Unit (fakes) → integration (testcontainers) → contract (adapters) → e2e (extension ↔ server, mock LLM) → load (k6/Locust at 10× target) → evals (free models only).
