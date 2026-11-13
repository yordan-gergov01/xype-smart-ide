"""No column may hold plaintext code, file names, symbols, queries or answers.

Every text and JSON column is on a reviewed allow-list with the reason it is safe;
a new one fails this test until it is reviewed and added. Content columns are
BYTEA (`*_enc`), and code-derived identifiers are CHAR(64) HMAC hex with a CHECK.
"""

from sqlalchemy import CHAR, LargeBinary, String, Text
from sqlalchemy.dialects.postgresql import JSONB

from common_lib.models import Base

# column -> why its value is not code, a path, a symbol, a query or an answer.
REVIEWED_TEXT_COLUMNS = {
    "teams.name": "metadata",
    "users.email": "account identity",
    "users.display_name": "account identity",
    "users.role": "enum (CHECK)",
    "users.client_id": "credential id, not the secret",
    "users.client_secret_hash": "peppered HMAC-SHA256 of a random secret (ADR-0014)",
    "projects.name": "metadata",
    "projects.external_ref": "external id",
    "projects.jira_project_key": "external id",
    "repositories.name": "repository metadata (repo name, not a file)",
    "repositories.remote_url": "repository metadata; CHECK forbids credentials",
    "repositories.template_kind": "fixed vocabulary",
    "repositories.hmac_key_id": "key id, not key material",
    "llm_models.id": "model id",
    "llm_models.name": "model name",
    "llm_models.provider": "enum (CHECK)",
    "llm_models.model_version": "version string",
    "llm_models.best_for_tasks": "JSON array of task type names",
    "llm_models.fallback_model_id": "model id",
    "user_preferences.model_id": "model id",
    "chat_messages.role": "enum (CHECK)",
    "chat_messages.model_id": "model id",
    "agent_tasks.idempotency_key": "client-generated id",
    "agent_tasks.task_type": "fixed vocabulary",
    "agent_tasks.status": "enum (CHECK)",
    "agent_tasks.jira_issue_key": "external id",
    "agent_tasks.parameters": "non-sensitive options; validated against typed models at the DAL",
    "agent_tasks.model_id": "model id",
    "agent_tasks.error_code": "fixed vocabulary",
    "events.queue": "fixed vocabulary",
    "events.event_type": "fixed vocabulary",
    "events.message_id": "broker id",
    "events.idempotency_key": "client-generated id",
    "events.correlation_id": "correlation id",
    "events.payload_meta": "sizes and durations; validated against typed models at the DAL",
    "code_chunks.chunk_kind": "fixed vocabulary (function, class, block), never a name",
    "code_chunks.language_id": "language id",
    "code_chunks.embedding_model_id": "model id",
    "code_chunks.embedding_model_version": "version string",
    "cost_ledger.model_id": "model id",
    "cost_ledger.task_type": "fixed vocabulary",
    "cost_ledger.request_id": "correlation id",
    "feedback.target_type": "enum (CHECK)",
    "feedback.target_id": "id",
    "feedback.model_id": "model id",
    "feedback.prompt_version": "version string",
    "eval_runs.suite": "fixed vocabulary",
    "eval_runs.model_id": "model id",
    "eval_runs.model_version": "version string",
    "eval_runs.prompt_version": "version string",
    "eval_runs.dataset_version": "version string",
    "eval_runs.status": "enum (CHECK)",
    "eval_runs.summary": "aggregate scores of seeded datasets",
    "eval_results.case_id": "dataset case id",
    "eval_results.scorer": "fixed vocabulary",
    "eval_results.details": "scores of seeded datasets, never proprietary code",
}


def _columns_of(kind: tuple[type, ...], *, exclude: tuple[type, ...] = ()) -> set[str]:
    return {
        f"{table.name}.{column.name}"
        for table in Base.metadata.sorted_tables
        for column in table.columns
        if isinstance(column.type, kind) and not isinstance(column.type, exclude)
    }


def test_every_text_and_json_column_is_reviewed() -> None:
    columns = _columns_of((String, Text, JSONB), exclude=(CHAR,))
    assert columns - REVIEWED_TEXT_COLUMNS.keys() == set(), "review and allow-list new columns"
    assert REVIEWED_TEXT_COLUMNS.keys() - columns == set(), "remove stale allow-list entries"


def test_code_derived_identifiers_are_hmac_hex_with_a_check() -> None:
    for table in Base.metadata.sorted_tables:
        checks = {c.name for c in table.constraints if c.name}
        for column in table.columns:
            if isinstance(column.type, CHAR) and column.type.length == 64:
                assert f"ck_{table.name}_{column.name}_hex" in checks, f"{table.name}.{column.name}"


def test_content_columns_are_encrypted_bytes() -> None:
    for name in _columns_of((LargeBinary,)):
        assert name.endswith("_enc"), name
