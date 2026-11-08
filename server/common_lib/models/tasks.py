"""Agent tasks and their queue events (brief §3.5, §3.6; ADR-0004)."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    LargeBinary,
    SmallInteger,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from common_lib.models.base import Base, created_at, updated_at, uuid_pk

TASK_STATUSES = (
    "QUEUED",
    "STARTED",
    "IN_PROGRESS",
    "FINISHED",
    "FAILED",
    "CANCELLED",
    "DEFERRED",
    "WAITING_FOR_CLIENT",
)


class AgentTask(Base):
    __tablename__ = "agent_tasks"
    __table_args__ = (
        CheckConstraint(f"status IN {TASK_STATUSES}", name="status"),
        UniqueConstraint("user_id", "idempotency_key"),
        Index("idx_usr_status", "user_id", "status"),
        Index(
            "idx_agent_tasks_deferred",
            "scheduled_for",
            postgresql_where=text("status = 'DEFERRED'"),
        ),
    )

    id: Mapped[uuid_pk]
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id"))
    idempotency_key: Mapped[str]
    task_type: Mapped[str]
    status: Mapped[str] = mapped_column(server_default="QUEUED")
    priority: Mapped[int] = mapped_column(SmallInteger, server_default="4")
    jira_issue_key: Mapped[str | None]
    instruction_enc: Mapped[bytes] = mapped_column(LargeBinary)
    # Non-sensitive options only; reason: free-form JSON object.
    parameters: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    result_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    run_now: Mapped[bool] = mapped_column(server_default=text("false"))
    # CostScheduler deferral.
    scheduled_for: Mapped[datetime | None]
    model_id: Mapped[str | None] = mapped_column(ForeignKey("llm_models.id"))
    error_code: Mapped[str | None]
    created_at: Mapped[created_at]
    started_at: Mapped[datetime | None]
    completed_at: Mapped[datetime | None]
    updated_at: Mapped[updated_at]


class TaskEvent(Base):
    """Queue events, attempts and backoff; idempotent per key, type and attempt."""

    __tablename__ = "events"
    __table_args__ = (
        UniqueConstraint("idempotency_key", "event_type", "attempt"),
        Index("idx_events_task_created", "task_id", "created_at"),
    )

    id: Mapped[uuid_pk]
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_tasks.id", ondelete="CASCADE")
    )
    # tasks.interactive | tasks.agent | tasks.batch
    queue: Mapped[str]
    # enqueued, started, step, retry, dead_lettered, ...
    event_type: Mapped[str]
    message_id: Mapped[str]
    idempotency_key: Mapped[str]
    attempt: Mapped[int] = mapped_column(SmallInteger, server_default="0")
    next_attempt_at: Mapped[datetime | None]
    correlation_id: Mapped[str]
    # Sizes and durations, never content; reason: free-form JSON object.
    payload_meta: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    created_at: Mapped[created_at]
