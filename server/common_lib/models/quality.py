"""Cost ledger, feedback and evaluation runs. Money is USD."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CHAR,
    REAL,
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    LargeBinary,
    Numeric,
    SmallInteger,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from common_lib.models.base import Base, created_at, uuid_pk


class CostLedgerEntry(Base):
    __tablename__ = "cost_ledger"
    __table_args__ = (
        Index("idx_cost_ledger_user_time", "user_id", "occurred_at"),
        Index("idx_cost_ledger_project_time", "project_id", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    occurred_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id"))
    model_id: Mapped[str] = mapped_column(ForeignKey("llm_models.id"))
    task_type: Mapped[str]
    request_id: Mapped[str]
    input_tokens: Mapped[int] = mapped_column(server_default="0")
    input_cached_tokens: Mapped[int] = mapped_column(server_default="0")
    output_tokens: Mapped[int] = mapped_column(server_default="0")
    is_peak: Mapped[bool]
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), server_default="0")


class Feedback(Base):
    __tablename__ = "feedback"
    __table_args__ = (
        CheckConstraint(
            "target_type IN ('chat_message', 'completion', 'agent_task')", name="target_type"
        ),
        CheckConstraint("rating IN (-1, 1)", name="rating"),
        UniqueConstraint("user_id", "target_type", "target_id"),
        Index("idx_feedback_model_prompt", "model_id", "prompt_version", "created_at"),
    )

    id: Mapped[uuid_pk]
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    target_type: Mapped[str]
    target_id: Mapped[str]
    rating: Mapped[int] = mapped_column(SmallInteger)
    annotation_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    model_id: Mapped[str | None] = mapped_column(ForeignKey("llm_models.id"))
    prompt_version: Mapped[str | None]
    created_at: Mapped[created_at]


class EvalRun(Base):
    __tablename__ = "eval_runs"
    __table_args__ = (
        CheckConstraint("status IN ('RUNNING', 'PASSED', 'FAILED', 'ERROR')", name="status"),
        Index("idx_eval_runs_suite_model", "suite", "model_id", text("started_at DESC")),
    )

    id: Mapped[uuid_pk]
    # completion | chat | agentic | mutation | negative
    suite: Mapped[str]
    model_id: Mapped[str] = mapped_column(ForeignKey("llm_models.id"))
    model_version: Mapped[str]
    prompt_version: Mapped[str]
    dataset_version: Mapped[str]
    git_sha: Mapped[str] = mapped_column(CHAR(40))
    baseline_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("eval_runs.id"))
    status: Mapped[str]
    # Aggregate scores; reason: free-form JSON object.
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    started_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    finished_at: Mapped[datetime | None]


class EvalResult(Base):
    __tablename__ = "eval_results"
    __table_args__ = (UniqueConstraint("run_id", "case_id", "scorer"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("eval_runs.id", ondelete="CASCADE"))
    case_id: Mapped[str]
    # exact | ast | tests | compiler | refusal
    scorer: Mapped[str]
    score: Mapped[float] = mapped_column(REAL)
    passed: Mapped[bool]
    latency_ms: Mapped[int | None]
    # No proprietary code; reason: free-form JSON object.
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
