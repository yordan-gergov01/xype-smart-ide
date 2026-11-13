"""`projects.company_os_ref` becomes `projects.external_ref`: the project's id in the
project-context system, read by the project-context connector.

Written by hand. A rename only: values are kept.

Revision ID: 0003
Revises: 0002
Created: 2026-11-12
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("projects", "company_os_ref", new_column_name="external_ref")


def downgrade() -> None:
    op.alter_column("projects", "external_ref", new_column_name="company_os_ref")
