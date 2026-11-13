"""`repository_keys`: each repository's HMAC key, wrapped under the KEK (ADR-0016), and a
stricter `repositories.remote_url` CHECK that allows only the forms registration stores.

Written by hand. The table is new and no repository was registered through an API
before this revision, so nothing is backfilled. The patterns are frozen here; they equal
`common_lib.remote_urls.REMOTE_URL_FORM` and `SCP_FORM` at this revision.

Revision ID: 0004
Revises: 0003
Created: 2026-11-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ALLOWED_FORM = (
    "remote_url ~ '^(https|ssh|git)://[a-z0-9-]+([.][a-z0-9-]+)+(:[0-9]{1,5})?(/[A-Za-z0-9._~%+/-]*)?$'"
    " OR remote_url ~ '^(git@)?[A-Za-z0-9-]+([.][A-Za-z0-9-]+)+:[A-Za-z0-9._~%+/-]+$'"
)
NO_CREDENTIALS = "remote_url !~ '^[a-zA-Z][a-zA-Z0-9+.-]*://[^/]*@'"


def upgrade() -> None:
    op.drop_constraint(
        op.f("ck_repositories_remote_url_no_credentials"), "repositories", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_repositories_remote_url_allowed_form"), "repositories", ALLOWED_FORM
    )
    op.create_table(
        "repository_keys",
        sa.Column("repository_id", sa.UUID(), nullable=False),
        sa.Column("hmac_key_id", sa.String(), nullable=False),
        sa.Column("kek_id", sa.String(), nullable=False),
        sa.Column("wrapped_key_enc", sa.LargeBinary(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "octet_length(wrapped_key_enc) = 60",
            name=op.f("ck_repository_keys_wrapped_key_length"),
        ),
        sa.ForeignKeyConstraint(
            ["repository_id"],
            ["repositories.id"],
            name=op.f("fk_repository_keys_repository_id_repositories"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("repository_id", "hmac_key_id", name=op.f("pk_repository_keys")),
    )
    op.create_index(
        "uq_repository_keys_active",
        "repository_keys",
        ["repository_id"],
        unique=True,
        postgresql_where=sa.text("retired_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_repository_keys_active", table_name="repository_keys")
    op.drop_table("repository_keys")
    op.drop_constraint(
        op.f("ck_repositories_remote_url_allowed_form"), "repositories", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_repositories_remote_url_no_credentials"), "repositories", NO_CREDENTIALS
    )
