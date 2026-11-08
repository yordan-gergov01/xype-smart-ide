"""Baseline schema: the 18 tables of the Phase 0 draft (docs/interfaces.md, ADR-0002).

Generated from common_lib.models and reviewed by hand. No column holds plaintext code,
file names, symbols, queries or answers; code-derived ids are CHECKed HMAC hex.

Revision ID: 0001
Revises: none
Created: 2026-11-08 13:35:54.587634
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Created by the superuser at database init; a no-op here, and a clear error if missing.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "llm_models",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("model_version", sa.String(), nullable=False),
        sa.Column("max_context_length", sa.Integer(), nullable=False),
        sa.Column(
            "price_input_miss_offpeak",
            sa.Numeric(precision=12, scale=6),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "price_input_hit_offpeak",
            sa.Numeric(precision=12, scale=6),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "price_output_offpeak",
            sa.Numeric(precision=12, scale=6),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "price_input_miss_peak",
            sa.Numeric(precision=12, scale=6),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "price_input_hit_peak",
            sa.Numeric(precision=12, scale=6),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "price_output_peak",
            sa.Numeric(precision=12, scale=6),
            server_default="0",
            nullable=False,
        ),
        sa.Column("is_paid", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "best_for_tasks",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("fallback_model_id", sa.String(), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "provider IN ('mock', 'ollama', 'vllm', 'openrouter', 'deepseek')",
            name=op.f("ck_llm_models_provider"),
        ),
        sa.ForeignKeyConstraint(
            ["fallback_model_id"],
            ["llm_models.id"],
            name=op.f("fk_llm_models_fallback_model_id_llm_models"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_llm_models")),
    )
    op.create_index(
        "idx_llm_models_best_for",
        "llm_models",
        ["best_for_tasks"],
        unique=False,
        postgresql_using="gin",
    )
    op.create_table(
        "teams",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_teams")),
        sa.UniqueConstraint("name", name=op.f("uq_teams_name")),
    )
    op.create_table(
        "eval_runs",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("suite", sa.String(), nullable=False),
        sa.Column("model_id", sa.String(), nullable=False),
        sa.Column("model_version", sa.String(), nullable=False),
        sa.Column("prompt_version", sa.String(), nullable=False),
        sa.Column("dataset_version", sa.String(), nullable=False),
        sa.Column("git_sha", sa.CHAR(length=40), nullable=False),
        sa.Column("baseline_run_id", sa.UUID(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column(
            "summary",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('RUNNING', 'PASSED', 'FAILED', 'ERROR')", name=op.f("ck_eval_runs_status")
        ),
        sa.ForeignKeyConstraint(
            ["baseline_run_id"],
            ["eval_runs.id"],
            name=op.f("fk_eval_runs_baseline_run_id_eval_runs"),
        ),
        sa.ForeignKeyConstraint(
            ["model_id"], ["llm_models.id"], name=op.f("fk_eval_runs_model_id_llm_models")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_eval_runs")),
    )
    op.create_index(
        "idx_eval_runs_suite_model",
        "eval_runs",
        ["suite", "model_id", sa.literal_column("started_at DESC")],
        unique=False,
    )
    op.create_table(
        "projects",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("team_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("company_os_ref", sa.String(), nullable=True),
        sa.Column("jira_project_key", sa.String(), nullable=True),
        sa.Column("budget_usd_month", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], name=op.f("fk_projects_team_id_teams")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_projects")),
        sa.UniqueConstraint("team_id", "name", name=op.f("uq_projects_team_id_name")),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("team_id", sa.UUID(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("role", sa.String(), server_default="developer", nullable=False),
        sa.Column("client_id", sa.String(), nullable=False),
        sa.Column("client_secret_hash", sa.String(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("last_login", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("role IN ('developer', 'admin')", name=op.f("ck_users_role")),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], name=op.f("fk_users_team_id_teams")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("client_id", name=op.f("uq_users_client_id")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
    )
    op.create_table(
        "agent_tasks",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=True),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column("task_type", sa.String(), nullable=False),
        sa.Column("status", sa.String(), server_default="QUEUED", nullable=False),
        sa.Column("priority", sa.SmallInteger(), server_default="4", nullable=False),
        sa.Column("jira_issue_key", sa.String(), nullable=True),
        sa.Column("instruction_enc", sa.LargeBinary(), nullable=False),
        sa.Column(
            "parameters",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("result_enc", sa.LargeBinary(), nullable=True),
        sa.Column("run_now", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=True),
        sa.Column("model_id", sa.String(), nullable=True),
        sa.Column("error_code", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('QUEUED', 'STARTED', 'IN_PROGRESS', 'FINISHED', 'FAILED', 'CANCELLED', 'DEFERRED', 'WAITING_FOR_CLIENT')",
            name=op.f("ck_agent_tasks_status"),
        ),
        sa.ForeignKeyConstraint(
            ["model_id"], ["llm_models.id"], name=op.f("fk_agent_tasks_model_id_llm_models")
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("fk_agent_tasks_project_id_projects")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_agent_tasks_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_tasks")),
        sa.UniqueConstraint(
            "user_id", "idempotency_key", name=op.f("uq_agent_tasks_user_id_idempotency_key")
        ),
    )
    op.create_index(
        "idx_agent_tasks_deferred",
        "agent_tasks",
        ["scheduled_for"],
        unique=False,
        postgresql_where=sa.text("status = 'DEFERRED'"),
    )
    op.create_index("idx_usr_status", "agent_tasks", ["user_id", "status"], unique=False)
    op.create_table(
        "cost_ledger",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=True),
        sa.Column("model_id", sa.String(), nullable=False),
        sa.Column("task_type", sa.String(), nullable=False),
        sa.Column("request_id", sa.String(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("input_cached_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("output_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("is_peak", sa.Boolean(), nullable=False),
        sa.Column(
            "cost_usd", sa.Numeric(precision=12, scale=6), server_default="0", nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["model_id"], ["llm_models.id"], name=op.f("fk_cost_ledger_model_id_llm_models")
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("fk_cost_ledger_project_id_projects")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_cost_ledger_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cost_ledger")),
    )
    op.create_index(
        "idx_cost_ledger_project_time", "cost_ledger", ["project_id", "occurred_at"], unique=False
    )
    op.create_index(
        "idx_cost_ledger_user_time", "cost_ledger", ["user_id", "occurred_at"], unique=False
    )
    op.create_table(
        "eval_results",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("case_id", sa.String(), nullable=False),
        sa.Column("scorer", sa.String(), nullable=False),
        sa.Column("score", sa.REAL(), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["eval_runs.id"],
            name=op.f("fk_eval_results_run_id_eval_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_eval_results")),
        sa.UniqueConstraint(
            "run_id", "case_id", "scorer", name=op.f("uq_eval_results_run_id_case_id_scorer")
        ),
    )
    op.create_table(
        "feedback",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("target_type", sa.String(), nullable=False),
        sa.Column("target_id", sa.String(), nullable=False),
        sa.Column("rating", sa.SmallInteger(), nullable=False),
        sa.Column("annotation_enc", sa.LargeBinary(), nullable=True),
        sa.Column("model_id", sa.String(), nullable=True),
        sa.Column("prompt_version", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "target_type IN ('chat_message', 'completion', 'agent_task')",
            name=op.f("ck_feedback_target_type"),
        ),
        sa.CheckConstraint("rating IN (-1, 1)", name=op.f("ck_feedback_rating")),
        sa.ForeignKeyConstraint(
            ["model_id"], ["llm_models.id"], name=op.f("fk_feedback_model_id_llm_models")
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_feedback_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_feedback")),
        sa.UniqueConstraint(
            "user_id",
            "target_type",
            "target_id",
            name=op.f("uq_feedback_user_id_target_type_target_id"),
        ),
    )
    op.create_index(
        "idx_feedback_model_prompt",
        "feedback",
        ["model_id", "prompt_version", "created_at"],
        unique=False,
    )
    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("token_hash", sa.CHAR(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "token_hash ~ '^[0-9a-f]{64}$'", name=op.f("ck_refresh_tokens_token_hash_hex")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_refresh_tokens_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_refresh_tokens")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_refresh_tokens_token_hash")),
    )
    op.create_index(
        "idx_refresh_tokens_user",
        "refresh_tokens",
        ["user_id"],
        unique=False,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.create_table(
        "repositories",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("team_id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("remote_url", sa.String(), nullable=True),
        sa.Column("is_template", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("template_kind", sa.String(), nullable=True),
        sa.Column("merkle_root_hash", sa.CHAR(length=64), nullable=True),
        sa.Column("hmac_key_id", sa.String(), nullable=False),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "merkle_root_hash ~ '^[0-9a-f]{64}$'", name=op.f("ck_repositories_merkle_root_hash_hex")
        ),
        sa.CheckConstraint(
            "remote_url !~ '^[a-zA-Z][a-zA-Z0-9+.-]*://[^/]*@'",
            name=op.f("ck_repositories_remote_url_no_credentials"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("fk_repositories_project_id_projects")
        ),
        sa.ForeignKeyConstraint(
            ["team_id"], ["teams.id"], name=op.f("fk_repositories_team_id_teams")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_repositories")),
        sa.UniqueConstraint("team_id", "name", name=op.f("uq_repositories_team_id_name")),
    )
    op.create_index(
        "idx_repositories_templates",
        "repositories",
        ["team_id"],
        unique=False,
        postgresql_where=sa.text("is_template"),
    )
    op.create_table(
        "session",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=True),
        sa.Column("session_metadata_enc", sa.LargeBinary(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("fk_session_project_id_projects")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_session_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_session")),
    )
    op.create_index(
        "idx_user_last_updated",
        "session",
        ["user_id", sa.literal_column("last_updated_at DESC")],
        unique=False,
    )
    op.create_table(
        "user_preferences",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("model_id", sa.String(), nullable=True),
        sa.Column("privacy_mode", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("max_tokens", sa.Integer(), server_default="4096", nullable=False),
        sa.Column("tokens_used", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column(
            "budget_usd_month",
            sa.Numeric(precision=10, scale=2),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "allow_paid_models", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["model_id"], ["llm_models.id"], name=op.f("fk_user_preferences_model_id_llm_models")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_user_preferences_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_user_preferences")),
    )
    op.create_table(
        "chat_messages",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("content_enc", sa.LargeBinary(), nullable=False),
        sa.Column("model_id", sa.String(), nullable=True),
        sa.Column("is_backup_model", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("alternative_index", sa.SmallInteger(), server_default="0", nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "role IN ('user', 'assistant', 'system')", name=op.f("ck_chat_messages_role")
        ),
        sa.ForeignKeyConstraint(
            ["model_id"], ["llm_models.id"], name=op.f("fk_chat_messages_model_id_llm_models")
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["session.id"],
            name=op.f("fk_chat_messages_session_id_session"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chat_messages")),
        sa.UniqueConstraint(
            "session_id",
            "sequence_number",
            "alternative_index",
            name=op.f("uq_chat_messages_session_id_sequence_number_alternative_index"),
        ),
    )
    op.create_index(
        "idx_session_created", "chat_messages", ["session_id", "created_at"], unique=False
    )
    op.create_table(
        "chunk_edges",
        sa.Column("repository_id", sa.UUID(), nullable=False),
        sa.Column("caller_symbol", sa.CHAR(length=64), nullable=False),
        sa.Column("callee_symbol", sa.CHAR(length=64), nullable=False),
        sa.CheckConstraint(
            "callee_symbol ~ '^[0-9a-f]{64}$'", name=op.f("ck_chunk_edges_callee_symbol_hex")
        ),
        sa.CheckConstraint(
            "caller_symbol ~ '^[0-9a-f]{64}$'", name=op.f("ck_chunk_edges_caller_symbol_hex")
        ),
        sa.ForeignKeyConstraint(
            ["repository_id"],
            ["repositories.id"],
            name=op.f("fk_chunk_edges_repository_id_repositories"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "repository_id", "caller_symbol", "callee_symbol", name=op.f("pk_chunk_edges")
        ),
    )
    op.create_index(
        "idx_chunk_edges_callee", "chunk_edges", ["repository_id", "callee_symbol"], unique=False
    )
    op.create_table(
        "code_chunks",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("repository_id", sa.UUID(), nullable=False),
        sa.Column("obfuscated_filename", sa.CHAR(length=64), nullable=False),
        sa.Column("encrypted_chunk_hash", sa.CHAR(length=64), nullable=False),
        sa.Column("line_start", sa.Integer(), nullable=False),
        sa.Column("line_end", sa.Integer(), nullable=False),
        sa.Column("chunk_kind", sa.String(), nullable=True),
        sa.Column("language_id", sa.String(), nullable=True),
        sa.Column("embedding_model_id", sa.String(), nullable=False),
        sa.Column("embedding_model_version", sa.String(), nullable=False),
        sa.Column("vector_embedding", Vector(1024), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "encrypted_chunk_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_code_chunks_encrypted_chunk_hash_hex"),
        ),
        sa.CheckConstraint(
            "obfuscated_filename ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_code_chunks_obfuscated_filename_hex"),
        ),
        sa.CheckConstraint("line_end >= line_start", name=op.f("ck_code_chunks_line_range")),
        sa.ForeignKeyConstraint(
            ["repository_id"],
            ["repositories.id"],
            name=op.f("fk_code_chunks_repository_id_repositories"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_code_chunks")),
        sa.UniqueConstraint(
            "repository_id",
            "encrypted_chunk_hash",
            "obfuscated_filename",
            name=op.f("uq_code_chunks_repository_id_encrypted_chunk_hash_obfuscated_filename"),
        ),
    )
    op.create_index(
        "idx_code_chunks_embedding_hnsw",
        "code_chunks",
        ["vector_embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_with={"m": 16, "ef_construction": 64},
        postgresql_ops={"vector_embedding": "vector_cosine_ops"},
    )
    op.create_index(
        "idx_code_chunks_repo_file",
        "code_chunks",
        ["repository_id", "obfuscated_filename"],
        unique=False,
    )
    op.create_table(
        "events",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("task_id", sa.UUID(), nullable=True),
        sa.Column("queue", sa.String(), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("message_id", sa.String(), nullable=False),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column("attempt", sa.SmallInteger(), server_default="0", nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("correlation_id", sa.String(), nullable=False),
        sa.Column(
            "payload_meta",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["agent_tasks.id"],
            name=op.f("fk_events_task_id_agent_tasks"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_events")),
        sa.UniqueConstraint(
            "idempotency_key",
            "event_type",
            "attempt",
            name=op.f("uq_events_idempotency_key_event_type_attempt"),
        ),
    )
    op.create_index("idx_events_task_created", "events", ["task_id", "created_at"], unique=False)
    op.create_table(
        "merkle_nodes",
        sa.Column("repository_id", sa.UUID(), nullable=False),
        sa.Column("node_id", sa.CHAR(length=64), nullable=False),
        sa.Column("parent_id", sa.CHAR(length=64), nullable=True),
        sa.Column("hash", sa.CHAR(length=64), nullable=False),
        sa.Column("is_leaf", sa.Boolean(), nullable=False),
        sa.Column("chunk_id", sa.UUID(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("hash ~ '^[0-9a-f]{64}$'", name=op.f("ck_merkle_nodes_hash_hex")),
        sa.CheckConstraint("node_id ~ '^[0-9a-f]{64}$'", name=op.f("ck_merkle_nodes_node_id_hex")),
        sa.CheckConstraint(
            "parent_id ~ '^[0-9a-f]{64}$'", name=op.f("ck_merkle_nodes_parent_id_hex")
        ),
        sa.ForeignKeyConstraint(
            ["chunk_id"],
            ["code_chunks.id"],
            name=op.f("fk_merkle_nodes_chunk_id_code_chunks"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["repository_id"],
            ["repositories.id"],
            name=op.f("fk_merkle_nodes_repository_id_repositories"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("repository_id", "node_id", name=op.f("pk_merkle_nodes")),
    )
    op.create_index(
        "idx_merkle_nodes_parent", "merkle_nodes", ["repository_id", "parent_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("idx_merkle_nodes_parent", table_name="merkle_nodes")
    op.drop_table("merkle_nodes")
    op.drop_index("idx_events_task_created", table_name="events")
    op.drop_table("events")
    op.drop_index("idx_code_chunks_repo_file", table_name="code_chunks")
    op.drop_index(
        "idx_code_chunks_embedding_hnsw",
        table_name="code_chunks",
        postgresql_using="hnsw",
        postgresql_with={"m": 16, "ef_construction": 64},
        postgresql_ops={"vector_embedding": "vector_cosine_ops"},
    )
    op.drop_table("code_chunks")
    op.drop_index("idx_chunk_edges_callee", table_name="chunk_edges")
    op.drop_table("chunk_edges")
    op.drop_index("idx_session_created", table_name="chat_messages")
    op.drop_table("chat_messages")
    op.drop_table("user_preferences")
    op.drop_index("idx_user_last_updated", table_name="session")
    op.drop_table("session")
    op.drop_index(
        "idx_repositories_templates",
        table_name="repositories",
        postgresql_where=sa.text("is_template"),
    )
    op.drop_table("repositories")
    op.drop_index(
        "idx_refresh_tokens_user",
        table_name="refresh_tokens",
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.drop_table("refresh_tokens")
    op.drop_index("idx_feedback_model_prompt", table_name="feedback")
    op.drop_table("feedback")
    op.drop_table("eval_results")
    op.drop_index("idx_cost_ledger_user_time", table_name="cost_ledger")
    op.drop_index("idx_cost_ledger_project_time", table_name="cost_ledger")
    op.drop_table("cost_ledger")
    op.drop_index("idx_usr_status", table_name="agent_tasks")
    op.drop_index(
        "idx_agent_tasks_deferred",
        table_name="agent_tasks",
        postgresql_where=sa.text("status = 'DEFERRED'"),
    )
    op.drop_table("agent_tasks")
    op.drop_table("users")
    op.drop_table("projects")
    op.drop_index("idx_eval_runs_suite_model", table_name="eval_runs")
    op.drop_table("eval_runs")
    op.drop_table("teams")
    op.drop_index("idx_llm_models_best_for", table_name="llm_models", postgresql_using="gin")
    op.drop_table("llm_models")
