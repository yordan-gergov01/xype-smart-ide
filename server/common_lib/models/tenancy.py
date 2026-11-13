"""Teams, users, projects and registered repositories."""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import CHAR, CheckConstraint, ForeignKey, Index, Numeric, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from common_lib.models.base import Base, created_at, hmac_checks, updated_at, uuid_pk
from common_lib.remote_urls import REMOTE_URL_FORM, SCP_FORM


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[uuid_pk]
    name: Mapped[str] = mapped_column(unique=True)
    created_at: Mapped[created_at]


class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("role IN ('developer', 'admin')", name="role"),)

    id: Mapped[uuid_pk]
    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("teams.id"))
    email: Mapped[str] = mapped_column(unique=True)
    display_name: Mapped[str]
    role: Mapped[str] = mapped_column(server_default="developer")
    # Client credentials (ADR-0014). The 256-bit random secret is stored only as
    # `<pepper id>$<HMAC-SHA256(pepper, secret) hex>`; a slow hash adds nothing for it.
    client_id: Mapped[str] = mapped_column(unique=True)
    client_secret_hash: Mapped[str]
    is_active: Mapped[bool] = mapped_column(server_default=text("true"))
    last_login: Mapped[datetime | None]
    created_at: Mapped[created_at]
    updated_at: Mapped[updated_at]


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    __table_args__ = (
        Index(
            "idx_refresh_tokens_user",
            "user_id",
            postgresql_where=text("revoked_at IS NULL"),
        ),
        Index("idx_refresh_tokens_family", "family_id"),
        *hmac_checks("token_hash"),
    )

    id: Mapped[uuid_pk]
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    # HMAC-SHA256(pepper, token) hex (ADR-0014); the token itself is never stored.
    token_hash: Mapped[str] = mapped_column(CHAR(64), unique=True)
    # One family per login (IDE window); reuse of a revoked token revokes the family.
    family_id: Mapped[uuid.UUID]
    # The family's absolute limit (90 days), copied onto every token of the family, so it
    # survives the deletion of older rows.
    family_expires_at: Mapped[datetime]
    expires_at: Mapped[datetime]
    revoked_at: Mapped[datetime | None]
    created_at: Mapped[created_at]


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (UniqueConstraint("team_id", "name"),)

    id: Mapped[uuid_pk]
    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("teams.id"))
    name: Mapped[str]
    # The project's id in the project-context system (read by the project-context connector).
    external_ref: Mapped[str | None]
    jira_project_key: Mapped[str | None]
    budget_usd_month: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    created_at: Mapped[created_at]


class Repository(Base):
    """A registered repository. Its name and remote URL are metadata, not code, and
    may be stored in clear. `remote_url` never holds credentials or a local path:
    registration strips them, and a CHECK allows only the forms it produces."""

    __tablename__ = "repositories"
    __table_args__ = (
        UniqueConstraint("team_id", "name"),
        Index("idx_repositories_templates", "team_id", postgresql_where=text("is_template")),
        *hmac_checks("merkle_root_hash"),
        # Only the allowed forms: no userinfo, query, fragment or local path (ADR-0016).
        CheckConstraint(
            f"remote_url ~ '{REMOTE_URL_FORM}' OR remote_url ~ '{SCP_FORM}'",
            name="remote_url_allowed_form",
        ),
    )

    id: Mapped[uuid_pk]
    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("teams.id"))
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id"))
    name: Mapped[str]
    remote_url: Mapped[str | None]
    is_template: Mapped[bool] = mapped_column(server_default=text("false"))
    template_kind: Mapped[str | None]
    merkle_root_hash: Mapped[str | None] = mapped_column(CHAR(64))
    # Key used to obfuscate this repository's file ids.
    hmac_key_id: Mapped[str]
    last_synced_at: Mapped[datetime | None]
    created_at: Mapped[created_at]


class RepositoryKey(Base):
    """A repository's HMAC key (ADR-0016): random, created at registration, and stored
    only as `nonce ‖ AES-256-GCM ‖ tag` under `KEK_repo`, with the AAD binding the
    repository, the key id and the KEK id. At most one key per repository is active;
    a retired key is kept until the re-index under its successor finishes."""

    __tablename__ = "repository_keys"
    __table_args__ = (
        # A 32-byte key, a 12-byte nonce and a 16-byte tag.
        CheckConstraint("octet_length(wrapped_key_enc) = 60", name="wrapped_key_length"),
        Index(
            "uq_repository_keys_active",
            "repository_id",
            unique=True,
            postgresql_where=text("retired_at IS NULL"),
        ),
    )

    repository_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), primary_key=True
    )
    hmac_key_id: Mapped[str] = mapped_column(primary_key=True)
    kek_id: Mapped[str]
    wrapped_key_enc: Mapped[bytes]
    created_at: Mapped[created_at]
    retired_at: Mapped[datetime | None]
