"""Model catalogue and per-user preferences. All money is USD."""

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, Numeric, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from common_lib.models.base import Base, updated_at

PROVIDERS = ("mock", "ollama", "vllm", "openrouter", "deepseek")

# USD per 1M tokens.
price = Numeric(12, 6)


class LlmModel(Base):
    __tablename__ = "llm_models"
    __table_args__ = (
        CheckConstraint(f"provider IN {PROVIDERS}", name="provider"),
        Index("idx_llm_models_best_for", "best_for_tasks", postgresql_using="gin"),
    )

    # e.g. 'ollama:qwen2.5-coder:1.5b'
    id: Mapped[str] = mapped_column(primary_key=True)
    name: Mapped[str]
    provider: Mapped[str]
    # Part of every cache key.
    model_version: Mapped[str]
    max_context_length: Mapped[int]
    price_input_miss_offpeak: Mapped[Decimal] = mapped_column(price, server_default="0")
    price_input_hit_offpeak: Mapped[Decimal] = mapped_column(price, server_default="0")
    price_output_offpeak: Mapped[Decimal] = mapped_column(price, server_default="0")
    price_input_miss_peak: Mapped[Decimal] = mapped_column(price, server_default="0")
    price_input_hit_peak: Mapped[Decimal] = mapped_column(price, server_default="0")
    price_output_peak: Mapped[Decimal] = mapped_column(price, server_default="0")
    is_paid: Mapped[bool] = mapped_column(server_default=text("false"))
    # e.g. ["completion", "chat"]; reason: JSON array of task type names.
    best_for_tasks: Mapped[list[Any]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    # Backup chain (brief §3.9).
    fallback_model_id: Mapped[str | None] = mapped_column(ForeignKey("llm_models.id"))
    enabled: Mapped[bool] = mapped_column(server_default=text("false"))
    updated_at: Mapped[updated_at]


class UserPreferences(Base):
    __tablename__ = "user_preferences"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    model_id: Mapped[str | None] = mapped_column(ForeignKey("llm_models.id"))
    privacy_mode: Mapped[bool] = mapped_column(server_default=text("true"))
    max_tokens: Mapped[int] = mapped_column(server_default="4096")
    # Current month, denormalised from the cost ledger.
    tokens_used: Mapped[int] = mapped_column(BigInteger, server_default="0")
    budget_usd_month: Mapped[Decimal] = mapped_column(Numeric(10, 2), server_default="0")
    allow_paid_models: Mapped[bool] = mapped_column(server_default=text("false"))
    updated_at: Mapped[updated_at]
