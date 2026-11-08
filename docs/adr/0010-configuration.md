# 0010 — Configuration via pydantic-settings, environment only

- Status: Proposed
- Date: 2026-11-08
- Deciders: project owner
- Brief reference: §1 (12-factor), §2

## Context and problem statement

Services run locally in compose and later in a hosted environment. The brief requires 12-factor, environment-driven configuration with no hard-coded hosts.

## Decision drivers

- One typed source of configuration per service.
- Secrets never printed, logged or echoed in errors.
- Fail fast at startup with a clear message.

## Considered options

1. **pydantic-settings, environment variables only.**
2. Config files per environment (YAML/TOML) plus environment overrides.

## Decision outcome

Chosen option: **1** (same as template_core).

- `common_lib.settings.ServiceSettings`: environment only, case-insensitive, frozen, `hide_input_in_errors`. Shared fields: `LOG_LEVEL`, `LOG_FORMAT`, `OTEL_EXPORTER_OTLP_ENDPOINT`.
- One subclass per service (`backend.core.settings.BackendSettings`); secrets are `SecretStr`.
- Hosts and credentials have no defaults: where a dependency lives is deployment configuration. Ports and tuning values have defaults.
- `load_settings` stops the process with one line per invalid variable (name and reason, never the value).
- Nothing else reads `os.environ`. Variables are documented in `deploy/env/.env.example`; a test keeps it equal to the compose file.

### Consequences

- Good: configuration is typed and validated before anything starts; no secret leaks through errors.
- Bad: many variables in compose; no hierarchical config.

### Confirmation

`tests/unit/common_lib/test_observability.py` (errors name the variable, never the value) and `tests/unit/deploy/test_compose.py` (env example matches compose).
