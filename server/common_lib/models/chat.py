"""Chat sessions and messages. Content is stored encrypted only."""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    LargeBinary,
    SmallInteger,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from common_lib.models.base import Base, created_at, uuid_pk


class ChatSession(Base):
    __tablename__ = "session"
    __table_args__ = (Index("idx_user_last_updated", "user_id", text("last_updated_at DESC")),)

    id: Mapped[uuid_pk]
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id"))
    # Encrypted name and description.
    session_metadata_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    created_at: Mapped[created_at]
    last_updated_at: Mapped[datetime] = mapped_column(server_default=text("now()"))


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    __table_args__ = (
        CheckConstraint("role IN ('user', 'assistant', 'system')", name="role"),
        UniqueConstraint("session_id", "sequence_number", "alternative_index"),
        Index("idx_session_created", "session_id", "created_at"),
    )

    id: Mapped[uuid_pk]
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("session.id", ondelete="CASCADE"))
    # Strict ordering within a session.
    sequence_number: Mapped[int]
    role: Mapped[str]
    content_enc: Mapped[bytes] = mapped_column(LargeBinary)
    model_id: Mapped[str | None] = mapped_column(ForeignKey("llm_models.id"))
    is_backup_model: Mapped[bool] = mapped_column(server_default=text("false"))
    alternative_index: Mapped[int] = mapped_column(SmallInteger, server_default="0")
    input_tokens: Mapped[int | None]
    output_tokens: Mapped[int | None]
    created_at: Mapped[created_at]
