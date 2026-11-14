# 0004 — RabbitMQ 4.4 quorum queues with native priorities and delayed delivery

- Status: Accepted (approved by the project owner on 2026-11-07; deviates from the brief's wording)
- Date: 2026-11-07
- Brief reference: §1 Queue, §3.9 Sync/async hybrid

## Context and problem statement

The brief asks for priority queues via `x-max-priority`, dead-letter queues, delayed retries and publisher confirms. The current RabbitMQ docs (4.4) change the picture:

- Quorum queues are the recommended default for durable, replicated queues; classic mirroring was removed in 4.0.
- Quorum queues always provide priorities 0–31 (strict dispatch since 4.3) and **ignore** `x-max-priority`.
- Quorum queues support at-least-once dead-lettering, `delivery-limit`, per-message TTL, and built-in delayed delivery through the `x-opt-delivery-time` header, with no plugin.

## Decision drivers

- Correct, supported features on the current major version.
- Fewer moving parts (no community plugin).
- Chat must be consumed before agent tasks without starving agents.

## Considered options

1. **Quorum queues**, native priorities, native delayed delivery, at-least-once DLX.
2. Classic queues with `x-max-priority` + `rabbitmq_delayed_message_exchange` plugin (the brief's literal wording).
3. Classic queues with TTL + DLX "retry ladders" for delays.

## Decision outcome

Chosen option: **1**.

- Exchanges: `xype.tasks` (direct), `xype.tasks.dlx` (dead-letter).
- Queues (quorum): `tasks.interactive` (chat; priority 8), `tasks.agent` (agents; priority 4), `tasks.batch` (off-peak batch, evals; priority 1), each with a `.dlq` partner.
- Starvation guard: chat and agent traffic live in **separate queues with separate consumer pools**, so strict priority only orders work *within* a queue. This also keeps the brief's intent ("chat consumed before agents") through worker allocation.
- Retries: consumer nacks are not used for backoff. A failed handler republishes with `x-opt-delivery-time` = now + 1, 2, 4 minutes (jitter), `x-attempt` header; after 3 retries it goes to the DLQ. `delivery-limit` = 5 as a safety net for crash loops.
- Publisher confirms on every publish; idempotency key in the message and a unique constraint in `events`.
- aio-pika 10.2.2 as the client. Phase 5 spike verifies that `x-opt-delivery-time` works through aio-pika headers on 4.4.

### Consequences

- Good: replicated, supported queue type; no plugin; built-in delay.
- Bad: needs RabbitMQ ≥ 4.4 (pinned image). Returned messages lose their priority (documented RabbitMQ behaviour), irrelevant because retries republish.
- Bad: if the Phase 5 spike shows delayed delivery is not usable from aio-pika, fall back to option 3 (TTL + DLX) — no plugin either way.

### Confirmation

Phase 5 integration tests (testcontainers RabbitMQ 4.4): priority ordering, retry delays, DLQ after 3 failures, kill-a-worker redelivery.

### Implementation notes (Phase 2 slice 8a)

The index queues (ADR-0015) come first and follow this ADR's retry plan through option 3's mechanism: a failed message is republished to a delay queue (TTL about 1, 2 and 4 minutes, then dead-lettered back to its queue) with an attempt header, and dead-lettered after three retries; the consumer never sleeps. No jitter yet: one consumer per queue retries in arrival order (jitter joins the task queues in Phase 5). The quorum queue's `x-delivery-limit` does not count an explicit `basic.nack` with requeue in RabbitMQ 4, so it only catches consumers that die mid-message. The exchanges and queues are declared by the broker's definitions, not by the services.

## More information

- https://www.rabbitmq.com/docs/priority
- https://www.rabbitmq.com/docs/quorum-queues
