"""The LLM worker (ADR-0006): consumes jobs from RabbitMQ and answers them with a
provider. No database access. The worker process arrives in Phase 3; the provider
seam and the mock exist from Phase 1 slice 6."""
