-- XYPE IDE — PostgreSQL 16 + pgvector 0.8.7 schema DRAFT (Phase 0).
-- Becomes the first Alembic revision in Phase 1 (ADR-0006); SQLAlchemy 2 models mirror it.
-- Rules:
--   * No plaintext code, file names, queries or answers in any column (brief §3.6, §3.7).
--   * Encrypted columns are BYTEA: AES-256-GCM envelope (key_id || nonce || ciphertext||tag).
--   * Obfuscated identifiers are HMAC-SHA256 hex strings (CHAR(64)).
--   * Vector dimension 1024 (ADR-0002); the migration reads EMBEDDER_DIM.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;   -- gen_random_uuid()

-- ------------------------------------------------------------------ tenancy

CREATE TABLE teams (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT NOT NULL UNIQUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    team_id         UUID NOT NULL REFERENCES teams(id),
    email           TEXT NOT NULL UNIQUE,
    display_name    TEXT NOT NULL,
    role            TEXT NOT NULL DEFAULT 'developer'
                    CHECK (role IN ('developer', 'admin')),
    client_id       TEXT NOT NULL UNIQUE,          -- client credentials (ADR-0014)
    client_secret_hash TEXT NOT NULL,              -- argon2id
    is_active       BOOLEAN NOT NULL DEFAULT true,
    last_login      TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE refresh_tokens (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash  CHAR(64) NOT NULL UNIQUE,
    expires_at  TIMESTAMPTZ NOT NULL,
    revoked_at  TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_refresh_tokens_user ON refresh_tokens (user_id) WHERE revoked_at IS NULL;

CREATE TABLE projects (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    team_id             UUID NOT NULL REFERENCES teams(id),
    name                TEXT NOT NULL,
    company_os_ref      TEXT,                       -- external id in Company OS
    jira_project_key    TEXT,
    budget_usd_month    NUMERIC(10, 2),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (team_id, name)
);

CREATE TABLE repositories (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    team_id             UUID NOT NULL REFERENCES teams(id),
    project_id          UUID REFERENCES projects(id),
    -- Repo display name and remote URL are org metadata, not code; allowed in clear.
    name                TEXT NOT NULL,
    remote_url          TEXT,
    is_template         BOOLEAN NOT NULL DEFAULT false,
    template_kind       TEXT,                       -- 'fastapi', 'react-ts', 'deploy', ...
    merkle_root_hash    CHAR(64),
    hmac_key_id         TEXT NOT NULL,              -- key used to obfuscate file ids
    last_synced_at      TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (team_id, name)
);
CREATE INDEX idx_repositories_templates ON repositories (team_id) WHERE is_template;

-- ------------------------------------------------------------------ models and preferences

CREATE TABLE llm_models (
    id                          TEXT PRIMARY KEY,   -- e.g. 'deepseek-v4-pro', 'ollama:qwen2.5-coder:1.5b'
    name                        TEXT NOT NULL,
    provider                    TEXT NOT NULL
                                CHECK (provider IN ('mock', 'ollama', 'vllm', 'openrouter', 'deepseek')),
    model_version               TEXT NOT NULL,      -- part of every cache key
    max_context_length          INTEGER NOT NULL,
    -- Prices in USD per 1M tokens (DeepSeek bills in USD); off-peak and peak.
    -- All cost accounting (cost_ledger, budgets) is in USD; no currency conversion.
    price_input_miss_offpeak    NUMERIC(12, 6) NOT NULL DEFAULT 0,
    price_input_hit_offpeak     NUMERIC(12, 6) NOT NULL DEFAULT 0,
    price_output_offpeak        NUMERIC(12, 6) NOT NULL DEFAULT 0,
    price_input_miss_peak       NUMERIC(12, 6) NOT NULL DEFAULT 0,
    price_input_hit_peak        NUMERIC(12, 6) NOT NULL DEFAULT 0,
    price_output_peak           NUMERIC(12, 6) NOT NULL DEFAULT 0,
    is_paid                     BOOLEAN NOT NULL DEFAULT false,
    best_for_tasks              JSONB NOT NULL DEFAULT '[]'::jsonb,  -- ["completion","chat",...]
    fallback_model_id           TEXT REFERENCES llm_models(id),      -- backup chain (§3.9)
    enabled                     BOOLEAN NOT NULL DEFAULT false,
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_llm_models_best_for ON llm_models USING gin (best_for_tasks);

CREATE TABLE user_preferences (
    user_id             UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    model_id            TEXT REFERENCES llm_models(id),
    privacy_mode        BOOLEAN NOT NULL DEFAULT true,
    max_tokens          INTEGER NOT NULL DEFAULT 4096,
    tokens_used         BIGINT NOT NULL DEFAULT 0,      -- current month, denormalised
    budget_usd_month    NUMERIC(10, 2) NOT NULL DEFAULT 0,
    allow_paid_models   BOOLEAN NOT NULL DEFAULT false,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ------------------------------------------------------------------ chat

CREATE TABLE session (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    project_id      UUID REFERENCES projects(id),
    session_metadata_enc BYTEA,                          -- encrypted name/description 
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_user_last_updated ON session (user_id, last_updated_at DESC);

CREATE TABLE chat_messages (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id          UUID NOT NULL REFERENCES session(id) ON DELETE CASCADE,
    sequence_number     INTEGER NOT NULL,                -- strict ordering
    role                TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content_enc         BYTEA NOT NULL,                  -- encrypted content only
    model_id            TEXT REFERENCES llm_models(id),
    is_backup_model     BOOLEAN NOT NULL DEFAULT false,
    alternative_index   SMALLINT NOT NULL DEFAULT 0,
    input_tokens        INTEGER,
    output_tokens       INTEGER,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (session_id, sequence_number, alternative_index)
);
CREATE INDEX idx_session_created ON chat_messages (session_id, created_at);

-- ------------------------------------------------------------------ agents and queue

CREATE TABLE agent_tasks (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL REFERENCES users(id),
    project_id          UUID REFERENCES projects(id),
    idempotency_key     TEXT NOT NULL,
    task_type           TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'QUEUED'
                        CHECK (status IN ('QUEUED', 'STARTED', 'IN_PROGRESS', 'FINISHED',
                                          'FAILED', 'CANCELLED', 'DEFERRED', 'WAITING_FOR_CLIENT')),
    priority            SMALLINT NOT NULL DEFAULT 4,
    jira_issue_key      TEXT,
    instruction_enc     BYTEA NOT NULL,
    parameters          JSONB NOT NULL DEFAULT '{}'::jsonb,   -- non-sensitive options only
    result_enc          BYTEA,
    run_now             BOOLEAN NOT NULL DEFAULT false,
    scheduled_for       TIMESTAMPTZ,                      -- CostScheduler deferral
    model_id            TEXT REFERENCES llm_models(id),
    error_code          TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at          TIMESTAMPTZ,
    completed_at        TIMESTAMPTZ,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, idempotency_key)
);
CREATE INDEX idx_usr_status ON agent_tasks (user_id, status);
CREATE INDEX idx_agent_tasks_deferred ON agent_tasks (scheduled_for) WHERE status = 'DEFERRED';

-- Queue events, attempts and backoff (brief §3.5/§3.6).
CREATE TABLE events (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id             UUID REFERENCES agent_tasks(id) ON DELETE CASCADE,
    queue               TEXT NOT NULL,                   -- tasks.interactive | tasks.agent | tasks.batch
    event_type          TEXT NOT NULL,                   -- enqueued, started, step, retry, dead_lettered, ...
    message_id          TEXT NOT NULL,                   -- RabbitMQ message id
    idempotency_key     TEXT NOT NULL,
    attempt             SMALLINT NOT NULL DEFAULT 0,
    next_attempt_at     TIMESTAMPTZ,
    correlation_id      TEXT NOT NULL,
    payload_meta        JSONB NOT NULL DEFAULT '{}'::jsonb,  -- sizes, durations; never content
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (idempotency_key, event_type, attempt)        -- idempotent handlers
);
CREATE INDEX idx_events_task_created ON events (task_id, created_at);

-- ------------------------------------------------------------------ index

CREATE TABLE code_chunks (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    repository_id           UUID NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
    obfuscated_filename     CHAR(64) NOT NULL,           -- HMAC of path
    encrypted_chunk_hash    CHAR(64) NOT NULL,           -- HMAC of content (Merkle leaf)
    line_start              INTEGER NOT NULL,
    line_end                INTEGER NOT NULL,
    chunk_kind              TEXT,                         -- function | class | block (no names)
    language_id             TEXT,
    embedding_model_id      TEXT NOT NULL,
    embedding_model_version TEXT NOT NULL,
    vector_embedding        vector(1024) NOT NULL,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (line_end >= line_start),
    UNIQUE (repository_id, encrypted_chunk_hash, obfuscated_filename)
    -- Deliberately NO plaintext code column.
);
CREATE INDEX idx_code_chunks_embedding_hnsw ON code_chunks
    USING hnsw (vector_embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);
CREATE INDEX idx_code_chunks_repo_file ON code_chunks (repository_id, obfuscated_filename);

-- Call-graph edges between chunks (obfuscated), for dependent injection (brief §3.4.2).
CREATE TABLE chunk_edges (
    repository_id   UUID NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
    caller_symbol   CHAR(64) NOT NULL,                   -- HMAC of symbol
    callee_symbol   CHAR(64) NOT NULL,
    PRIMARY KEY (repository_id, caller_symbol, callee_symbol)
);
CREATE INDEX idx_chunk_edges_callee ON chunk_edges (repository_id, callee_symbol);

CREATE TABLE merkle_nodes (
    repository_id   UUID NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
    node_id         CHAR(64) NOT NULL,                   -- HMAC of path / chunk position
    parent_id       CHAR(64),
    hash            CHAR(64) NOT NULL,
    is_leaf         BOOLEAN NOT NULL,
    chunk_id        UUID REFERENCES code_chunks(id) ON DELETE SET NULL,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (repository_id, node_id)
);
CREATE INDEX idx_merkle_nodes_parent ON merkle_nodes (repository_id, parent_id);

-- ------------------------------------------------------------------ cost, feedback, evals

CREATE TABLE cost_ledger (
    id                  BIGSERIAL PRIMARY KEY,
    occurred_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    user_id             UUID NOT NULL REFERENCES users(id),
    project_id          UUID REFERENCES projects(id),
    model_id            TEXT NOT NULL REFERENCES llm_models(id),
    task_type           TEXT NOT NULL,
    request_id          TEXT NOT NULL,
    input_tokens        INTEGER NOT NULL DEFAULT 0,
    input_cached_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens       INTEGER NOT NULL DEFAULT 0,
    is_peak             BOOLEAN NOT NULL,
    cost_usd            NUMERIC(12, 6) NOT NULL DEFAULT 0
);
CREATE INDEX idx_cost_ledger_user_time ON cost_ledger (user_id, occurred_at);
CREATE INDEX idx_cost_ledger_project_time ON cost_ledger (project_id, occurred_at);

CREATE TABLE feedback (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL REFERENCES users(id),
    target_type         TEXT NOT NULL CHECK (target_type IN ('chat_message', 'completion', 'agent_task')),
    target_id           TEXT NOT NULL,
    rating              SMALLINT NOT NULL CHECK (rating IN (-1, 1)),
    annotation_enc      BYTEA,
    model_id            TEXT REFERENCES llm_models(id),
    prompt_version      TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, target_type, target_id)
);
CREATE INDEX idx_feedback_model_prompt ON feedback (model_id, prompt_version, created_at);

CREATE TABLE eval_runs (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    suite               TEXT NOT NULL,                   -- completion | chat | agentic | mutation | negative
    model_id            TEXT NOT NULL REFERENCES llm_models(id),
    model_version       TEXT NOT NULL,
    prompt_version      TEXT NOT NULL,
    dataset_version     TEXT NOT NULL,
    git_sha             CHAR(40) NOT NULL,
    baseline_run_id     UUID REFERENCES eval_runs(id),
    status              TEXT NOT NULL CHECK (status IN ('RUNNING', 'PASSED', 'FAILED', 'ERROR')),
    summary             JSONB NOT NULL DEFAULT '{}'::jsonb,  -- aggregate scores
    started_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at         TIMESTAMPTZ
);
CREATE INDEX idx_eval_runs_suite_model ON eval_runs (suite, model_id, started_at DESC);

CREATE TABLE eval_results (
    id              BIGSERIAL PRIMARY KEY,
    run_id          UUID NOT NULL REFERENCES eval_runs(id) ON DELETE CASCADE,
    case_id         TEXT NOT NULL,
    scorer          TEXT NOT NULL,                       -- exact | ast | tests | compiler | refusal
    score           REAL NOT NULL,
    passed          BOOLEAN NOT NULL,
    latency_ms      INTEGER,
    details         JSONB NOT NULL DEFAULT '{}'::jsonb,  -- no proprietary code
    UNIQUE (run_id, case_id, scorer)
);
