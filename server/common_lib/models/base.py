"""Declarative base and column helpers shared by every table.

Privacy rules for every model (brief §3.6, §3.7; .claude/rules/07-security-privacy.md):
no column holds plaintext code, file names, symbols, queries or answers. Content is
`BYTEA` (`*_enc`) holding a versioned envelope (version, key id, nonce, AES-256-GCM
ciphertext and tag; ADR-0035, implemented with the crypto in ADR-0015); identifiers
derived from code are HMAC-SHA256 hex (`CHAR(64)`, enforced by CHECK constraints).
Every text and JSON column is on the reviewed allow-list in
tests/unit/common_lib/test_models_privacy.py.
"""

import uuid
from datetime import datetime
from typing import Annotated

from sqlalchemy import CHAR, CheckConstraint, DateTime, MetaData, String, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, mapped_column

# Deterministic constraint names, so migrations do not depend on the database's.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    # Every timestamp is TIMESTAMPTZ.
    type_annotation_map = {datetime: DateTime(timezone=True)}  # noqa: RUF012 - SQLAlchemy API


uuid_pk = Annotated[
    uuid.UUID,
    mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")),
]
created_at = Annotated[datetime, mapped_column(server_default=func.now())]
# Set on insert by the database and on every ORM update; repositories using bulk
# `update()` statements set it themselves.
updated_at = Annotated[datetime, mapped_column(server_default=func.now(), onupdate=func.now())]
# HMAC-SHA256 hex of a path, symbol or content: the only form code identifiers take.
hmac_hex = Annotated[str, mapped_column(CHAR(64))]

HMAC_HEX_PATTERN = "^[0-9a-f]{64}$"


def hmac_checks(*columns: str) -> tuple[CheckConstraint, ...]:
    """CHECK constraints that let only 64-char lowercase hex into these columns, so a
    plaintext path or symbol cannot be stored even by mistake."""
    return tuple(
        CheckConstraint(f"{column} ~ '{HMAC_HEX_PATTERN}'", name=f"{column}_hex")
        for column in columns
    )


model_ref = Annotated[str, mapped_column(String)]
