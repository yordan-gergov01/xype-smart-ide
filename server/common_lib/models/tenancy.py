"""Teams, users, projects and registered repositories."""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import CHAR, CheckConstraint, ForeignKey, Index, Numeric, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from common_lib.models.base import Base, created_at, hmac_checks, updated_at, uuid_pk


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
    company_os_ref: Mapped[str | None]
    jira_project_key: Mapped[str | None]
    budget_usd_month: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    created_at: Mapped[created_at]


class Repository(Base):
    """A registered XYPE repository. Its name and remote URL are organisation
    metadata, not code, and may be stored in clear. `remote_url` never holds
    credentials: the registration API strips any userinfo (`https://token@host`)."""

    __tablename__ = "repositories"
    __table_args__ = (
        UniqueConstraint("team_id", "name"),
        Index("idx_repositories_templates", "team_id", postgresql_where=text("is_template")),
        *hmac_checks("merkle_root_hash"),
        # No userinfo (`https://user:token@host/...`) in the remote URL.
        CheckConstraint(
            r"remote_url !~ '^[a-zA-Z][a-zA-Z0-9+.-]*://[^/]*@'", name="remote_url_no_credentials"
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
