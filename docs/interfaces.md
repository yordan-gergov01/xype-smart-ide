# Core interfaces (Phase 0 draft)

Every external dependency sits behind one of these. Each has a real adapter, an in-memory fake and a shared contract test suite (brief §2). Services import only these Protocols; adapters are wired in the composition roots (`server/backend/main.py` with `backend/api/dependencies/services.py`, `server/llm/main.py`, `server/indexer/main.py`). Layout: ADR-0006.

Conventions:
- Async everywhere on I/O. Pure computation stays sync.
- Domain types are frozen `dataclass(slots=True)`. Shared ones live in `server/common_lib/domain.py`; feature-specific ones next to their Protocol.
- Errors are typed per module (`LLMTimeoutError`, `BudgetExceededError`, …), all subclasses of `XypeError`.
- `SecretBytes` is a small wrapper that zeroes its buffer on `wipe()` and never prints its contents (`__repr__` returns `<SecretBytes len=N>`).

## Where each interface lives (ADR-0006)

| Section | Protocol(s) | Location |
|---|---|---|
| Shared types | `RequestContext`, `ChunkRef`, `ModelRef`, `XypeError`, `DependencyUnavailableError` | `server/common_lib/domain.py`, `common_lib/errors.py` (implemented) |
| Shared types | `SecretBytes`, `Clock` (+ `SystemClock`) | `server/common_lib/secret_bytes.py`, `common_lib/clock.py` (implemented; `FakeClock` in `tests/support`) |
| llm | `LLMProvider` + resilience wrappers | Value types and errors in `server/common_lib/llm.py`; Protocol and `MockLLMProvider` in `server/llm/providers/` (implemented, contract in `tests/support/contracts/llm.py`); ollama, vllm, openai_compatible and the wrappers in Phase 3; the backend reaches the worker through RabbitMQ |
| embeddings | `Embedder` | `server/common_lib/embeddings/` (implemented): Protocol and `OllamaEmbedder`; `FakeEmbedder` and the contract in `tests/support`; TEI for hosting later. Used by the indexer (documents) and the backend (queries) |
| retrieval | `VectorStore`, `ChunkRepository` | `backend/services/retrieval/` (Protocols), `backend/dal/retrieval/` (pgvector) |
| indexing | `MerkleIndex` | `server/indexer/` + `backend/dal/indexing/` |
| cache | `Cache`, `SemanticCache` | `backend/services/cache/` (Protocol, key builders), `backend/dal/cache/` (Redis) |
| tasks | `TaskQueue`, `TaskHandler`, `CostScheduler` | `backend/services/tasks/` (Protocol), `backend/messaging/` (RabbitMQ) |
| router | `ModelRouter` | `backend/services/router/` |
| prompts | `PromptRegistry` | `backend/services/prompts/`; templates as files in `server/prompts/` |
| security | `CryptoBox`, `SecretScrubber`, `KeyStore`, `hmac_id` | `common_lib/security/` (implemented; shared by the backend and the indexer), `KeyStore` adapter `common_lib/keystore/` (Redis only); client side in `clients/xype-ai` |
| auth | `CredentialVerifier`, `UserStore`, `RefreshTokenStore`, `RateLimiter`, `RevocationPublisher`; values `Principal`, `Credential`, `AccessClaims`; `SessionKeyService` (`/session-key`, ADR-0015) | `backend/services/auth/` (seams), `backend/dal/auth/` (adapters), `common_lib/auth.py` (values and errors) |
| connectors | `ConnectorClient` | `backend/services/connectors/` (Protocols), adapters wired in `backend/main.py` |
| observability | `Metrics` | `common_lib` (used by every service); added when services emit metrics; HTTP metrics live in the transport layer (ADR-0011) |
| health | `DependencyProbe` | `backend/services/health/` (implemented): `name`, `async check()` raising `DependencyUnavailableError`; adapters in `backend/dal/health/`, `backend/messaging/health.py` |
| agents | agent loop, `ClientToolPort` | `backend/services/agents/`; `ToolExecutor` in `clients/xype-ai` |

Interfaces without a status above arrive with the phase that gives them a real adapter (owner's decision, Phase 1 slice 6): retrieval, indexing and security in Phase 2, cache in Phase 3, prompts and router in Phase 4, tasks and agents in Phase 5, connectors in Phase 6 (their API specs are open questions Q1a and Q7), `Metrics` with the first service metrics (ADR-0011). Each then gets its Protocol, a fake in `tests/support`, a real adapter and one shared contract suite in `tests/support/contracts`.

## Shared domain types (sketch)

```python
TaskType = Literal["completion", "chat", "refactor", "tests", "docs", "lint", "implement_ticket"]

@dataclass(frozen=True, slots=True)
class RequestContext:          # propagated everywhere; also feeds logs/metrics
    user_id: UUID
    team_id: UUID
    project_id: UUID | None
    correlation_id: str
    task_type: TaskType

@dataclass(frozen=True, slots=True)
class ChunkRef:
    repository_id: UUID
    obfuscated_file_id: str
    line_start: int
    line_end: int
    chunk_hash: str

@dataclass(frozen=True, slots=True)
class ModelRef:
    model_id: str
    model_version: str
```

## llm

```python
@dataclass(frozen=True, slots=True)
class Message:
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    tool_call_id: str | None = None

@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    json_schema: Mapping[str, object]

@dataclass(frozen=True, slots=True)
class LLMRequest:
    model: ModelRef
    messages: Sequence[Message]          # stable prefix first (brief §5 lever 1)
    max_tokens: int
    temperature: float = 0.0
    tools: Sequence[ToolSpec] = ()
    response_schema: Mapping[str, object] | None = None   # JSON-schema output
    stop: Sequence[str] = ()

@dataclass(frozen=True, slots=True)
class FimRequest:
    model: ModelRef
    prefix: str
    suffix: str
    max_tokens: int
    stop: Sequence[str] = ()

@dataclass(frozen=True, slots=True)
class TokenUsage:
    input_tokens: int
    input_cached_tokens: int
    output_tokens: int

@dataclass(frozen=True, slots=True)
class LLMResponse:
    text: str
    tool_calls: Sequence["ToolCall"]
    usage: TokenUsage
    model: ModelRef
    finish_reason: Literal["stop", "length", "tool_calls", "error"]

@dataclass(frozen=True, slots=True)
class StreamEvent:
    delta: str
    tool_call: "ToolCall | None" = None
    usage: TokenUsage | None = None      # present on the final event

class LLMProvider(Protocol):
    """Adapters: mock, ollama, vllm, openai_compatible (profiles: openrouter, deepseek).
    Resilience wrappers also implement it."""
    name: str
    async def generate(self, request: LLMRequest, *, timeout_s: float) -> LLMResponse: ...
    def stream(self, request: LLMRequest, *, timeout_s: float) -> AsyncIterator[StreamEvent]: ...
    async def complete_fim(self, request: FimRequest, *, timeout_s: float) -> LLMResponse: ...
    async def health(self) -> bool: ...
```

Resilience wrappers are decorators around `LLMProvider` (same interface): `TimeoutProvider`, `CircuitBreakerProvider`, `RetryProvider`, `RaceToResponseProvider(primary, backup, first_wait_ms=200, grace_ms=100)`.

## embeddings

```python
class Embedder(Protocol):
    model: ModelRef
    dimension: int
    async def embed_documents(self, texts: Sequence[SecretBytes]) -> Sequence[Sequence[float]]:
        """Embeds and wipes the inputs before returning (stateless, brief §3.4.7)."""
    async def embed_query(self, text: str, *, instruction: str) -> Sequence[float]: ...
```

## retrieval

```python
@dataclass(frozen=True, slots=True)
class VectorRecord:
    chunk: ChunkRef          # carries leaf_node_id, the upsert key (ADR-0020)
    vector: Sequence[float]
    model: ModelRef

@dataclass(frozen=True, slots=True)
class SearchHit:
    chunk: ChunkRef
    score: float

class VectorStore(Protocol):
    """Adapters: pgvector (HNSW); turbopuffer later."""
    async def upsert(self, records: Sequence[VectorRecord]) -> None: ...
    async def delete(self, repository_id: UUID, leaf_node_ids: Sequence[str]) -> None: ...
    async def search(
        self, vector: Sequence[float], *, repository_ids: Sequence[UUID], k: int,
        min_score: float = 0.0,
    ) -> Sequence[SearchHit]: ...

class ChunkRepository(Protocol):
    """Metadata of chunks + call-graph edges (no code)."""
    async def get_by_file(self, repository_id: UUID, obfuscated_file_id: str) -> Sequence[ChunkRef]: ...
    async def dependents(self, repository_id: UUID, symbol_hashes: Sequence[str]) -> Sequence[ChunkRef]: ...
    async def replace_edges(self, repository_id: UUID, edges: Sequence[tuple[str, str]]) -> None: ...
```

## indexing

```python
@dataclass(frozen=True, slots=True)
class MerkleNodeView:
    node_id: str
    hash: str
    is_leaf: bool

@dataclass(frozen=True, slots=True)
class MerkleDiff:
    changed_leaves: Sequence[ChunkRef]
    deleted_node_ids: Sequence[str]

class MerkleIndex(Protocol):
    async def root(self, repository_id: UUID) -> str | None: ...
    async def children(self, repository_id: UUID, node_ids: Sequence[str]) -> Sequence[MerkleNodeView]: ...
    async def diff(
        self, repository_id: UUID, client_children: Mapping[str, Sequence[MerkleNodeView]],
    ) -> MerkleDiff:
        """Top-down comparison, O(log n) per changed leaf."""
    async def apply(self, repository_id: UUID, new_root: str, nodes: Sequence[MerkleNodeView]) -> None: ...
```

## cache

```python
class Cache(Protocol):
    """Cache-aside over Redis. Keys are built by `cache.keys` (normalised, versioned)."""
    async def get(self, key: str) -> bytes | None: ...
    async def set(self, key: str, value: bytes, *, ttl_s: int) -> None: ...
    async def delete_prefix(self, prefix: str) -> int: ...

class SemanticCache(Protocol):
    async def lookup(
        self, scope_key: str, query_vector: Sequence[float], *, min_similarity: float = 0.95,
    ) -> bytes | None: ...
    async def store(self, scope_key: str, query_vector: Sequence[float], answer: bytes, *, ttl_s: int) -> None: ...
    async def invalidate(self, scope_key: str) -> None: ...
```

Key builders (pure functions, not an interface): `vector_search_key(user_id, team_id, query_hash)`, `completion_key(team_id, model_id, model_version, file_id, snippet_hash)`, `explanation_key(team_id, model_id, model_version, query_hash)`, `embedding_key(model_id, model_version, chunk_hash)`.

## tasks

```python
Priority = Literal["interactive", "agent", "batch"]

@dataclass(frozen=True, slots=True)
class TaskMessage:
    task_id: UUID
    idempotency_key: str
    task_type: TaskType
    priority: Priority
    attempt: int
    correlation_id: str
    trace_parent: str
    payload: bytes                       # encrypted

class TaskQueue(Protocol):
    async def publish(self, message: TaskMessage, *, deliver_at: datetime | None = None) -> None:
        """Waits for the publisher confirm."""
    async def consume(self, priority: Priority, handler: "TaskHandler", *, prefetch: int) -> None: ...
    async def depth(self, priority: Priority) -> int: ...

class TaskHandler(Protocol):
    task_types: frozenset[TaskType]
    async def handle(self, message: TaskMessage) -> None:
        """Must be idempotent. Raise RetryableTaskError to retry with backoff, any other error → DLQ."""

class Clock(Protocol):
    def now(self) -> datetime: ...       # timezone-aware UTC

class CostScheduler(Protocol):
    def is_peak(self, at: datetime) -> bool: ...
    def next_offpeak_start(self, at: datetime) -> datetime: ...
    def schedule(self, task_type: TaskType, *, run_now: bool, at: datetime) -> datetime | None:
        """None = run immediately; otherwise the deferred start time."""
```

## router

```python
@dataclass(frozen=True, slots=True)
class RouteDecision:
    primary: ModelRef
    backups: Sequence[ModelRef]
    reason: str                          # "preference", "budget_degrade", "offpeak", ...

class ModelRouter(Protocol):
    async def route(self, ctx: RequestContext, *, estimated_input_tokens: int) -> RouteDecision: ...
```

## prompts

```python
@dataclass(frozen=True, slots=True)
class RenderedPrompt:
    template_id: str
    template_version: str
    messages: Sequence[Message]          # stable prefix, then volatile suffix
    stable_prefix_hash: str              # tracked for provider prefix-cache hit ratio

class PromptRegistry(Protocol):
    def render(
        self, template_id: str, *, model: ModelRef, variables: Mapping[str, object],
    ) -> RenderedPrompt:
        """Loads versioned files from server/prompts; picks a model-specific variant if present."""
    def version(self, template_id: str) -> str: ...
```

## security

```python
class CryptoBox:  # concrete: one implementation, no external dependency
    """AES-256-GCM under per-direction keys with counter nonces (ADR-0015)."""
    def __init__(self, *, sends: Direction) -> None: ...   # seals only this direction
    def seal(self, session_key: SecretBytes, counter: int, plaintext: SecretBytes,
             context: PayloadContext) -> EncryptedPayload: ...
    def open(self, session_key: SecretBytes, payload: EncryptedPayload,
             context: PayloadContext) -> SecretBytes: ...   # caller wipes

class SecretScrubber:  # concrete: the shared rules in packages/scrub-rules/ (ADR-0019)
    def scrub(self, text: str) -> ScrubResult: ...      # text with <SECRET_REMOVED>, counts per rule
```

`CryptoBox` and `SecretScrubber` were sketched as Protocols in Phase 0; they became concrete classes because each has exactly one implementation and no external dependency to fake (rule 02, no speculative seams). Key derivation and wrapping are plain functions in `common_lib/security/keys.py`.

Metadata obfuscation is a plain function in `security` — `hmac_id(repo_key: SecretBytes, domain: IdDomain, *fields: str) -> str` with length-prefixed fields (ADR-0016) — not an interface: one implementation, no external dependency.

```python
@dataclass(frozen=True, slots=True)
class SessionKeyRecord:          # who a key belongs to; never the key
    key_id: str
    user_id: str
    family_id: str
    created_at: datetime

class KeyStore(Protocol):
    """Session keys wrapped under the KEK in the `keystore` Redis (ADR-0015).
    Server side only: counters are allocated for s2c and recorded for c2s."""
    async def put(self, record: SessionKeyRecord, key: SecretBytes, *, ttl_seconds: int,
                  max_active: int, credentials_issued_at: datetime) -> None:
        """Never overwrites; refuses a revoked family or user (KeyRevokedError); then
        deletes the user's oldest other keys over `max_active`."""
    async def get(self, key_id: str) -> tuple[SessionKeyRecord, SecretBytes]: ...   # caller wipes
    async def bind_stream(self, key_id: str, session_id: str) -> None: ...  # one key, one stream
    async def advance_nonce(self, key_id: str, direction: Direction, counter: int) -> None:
        """Refuses a counter that is not above the highest seen."""
    async def next_counter(self, key_id: str, direction: Direction) -> int: ...  # atomic
    async def revoke_user(self, user_id: str, *, refuse_issued_until: datetime) -> None: ...  # + tombstone
    async def revoke_family(self, family_id: str) -> None: ...  # + tombstone

def check_key_age(record: SessionKeyRecord, now: datetime) -> None: ...  # 12 h, streams only

```

### auth (ADR-0014)

Values and errors live in `common_lib/auth.py`, so the adapters in `backend/dal/auth`
can return them without importing the service layer.

```python
@dataclass(frozen=True, slots=True)
class Principal:
    user_id: UUID
    team_id: UUID
    role: Literal["developer", "admin"]

@dataclass(frozen=True, slots=True)
class Credential:
    kind: Literal["client_credentials"]   # "oidc_id_token" later
    client_id: str
    secret: SecretBytes

class CredentialVerifier(Protocol):
    """Client credentials now, OIDC ID tokens later. Raises InvalidCredentialsError.
    Returns the principal and the stored state it was verified against."""
    async def verify(self, credential: Credential) -> VerifiedCredential: ...

class UserStore(Protocol):            # Postgres `users`, `teams`
    async def find_credentials(self, client_id: str) -> StoredCredentials | None: ...
    async def record_login(self, user_id: UUID, at: datetime) -> None: ...
    async def create_team(self, name: str) -> UUID: ...             # ConflictError
    async def find_team(self, name: str) -> UUID | None: ...
    async def create_user(self, *, team_id, email, display_name, role, client_id, secret_hash) -> UUID: ...
    async def set_active(self, client_id: str, *, active: bool) -> UUID | None: ...
    async def set_secret_hash(self, client_id: str, secret_hash: str) -> UUID | None: ...

class RefreshTokenStore(Protocol):    # Postgres `refresh_tokens`, by peppered hash
    async def add(self, token: NewRefreshToken, *, secret_hash: str) -> bool:
        """Only while the user is active and still has `secret_hash` (per-user lock)."""
    async def rotate(self, token_hashes: Sequence[str], new_token_hash: str, *,
                     now: datetime, refresh_ttl_seconds: int) -> Rotated | Reused | NotFound:
        """Atomic; reuse of a revoked token revokes its family."""
    async def revoke_user(self, user_id: UUID, *, now: datetime) -> None: ...

class RateLimiter(Protocol):          # Redis, GCRA in one Lua script; fails closed
    async def acquire(self, key: str, *, limit: int, period_seconds: int,
                      now: datetime, cost: int = 1) -> RateDecision: ...

class RevocationPublisher(Protocol):  # Redis channel `xype:auth:revoked`
    async def publish(self, revocation: Revocation) -> None: ...
```

`AuthService` (`login`, `refresh`, `authenticate`) and `ClientAdmin` (`create_team`,
`create_client`, `disable_client`, `rotate_secret`) are built from these in
`backend/core/auth.py`, shared by the composition roots `backend.main` and
`backend.admin_cli`.

## connectors

```python
@dataclass(frozen=True, slots=True)
class Ticket:
    key: str
    title: str
    status: str
    url: str

class ConnectorClient(Protocol):
    """Base for read-only context sources."""
    name: str
    async def health(self) -> bool: ...

class JiraClient(ConnectorClient, Protocol):
    async def my_tickets(self, user_email: str) -> Sequence[Ticket]: ...
    async def get_ticket(self, key: str) -> "TicketDetail": ...
    async def comment(self, key: str, body: str) -> None: ...
    async def transition(self, key: str, transition_name: str) -> None: ...

class CompanyOSClient(ConnectorClient, Protocol):
    async def project_context(self, project_ref: str) -> "ProjectContext": ...
    async def client_context(self, client_ref: str) -> "ClientContext": ...
    async def team_context(self, team_ref: str) -> "TeamContext": ...

class TemplateRegistry(ConnectorClient, Protocol):
    async def list_templates(self) -> Sequence["TemplateInfo"]: ...
    async def summary(self, template_id: str) -> str: ...  # stable-prefix material
```

## observability

```python
class Metrics(Protocol):
    def inc(self, name: str, *, labels: Mapping[str, str], value: float = 1.0) -> None: ...
    def observe(self, name: str, value: float, *, labels: Mapping[str, str]) -> None: ...
    def set_gauge(self, name: str, value: float, *, labels: Mapping[str, str]) -> None: ...
```

Label sets are fixed per metric (model, task_type, provider, cache, outcome) to keep cardinality bounded; user/project cost goes to `cost_ledger`, not to Prometheus labels.

## agents (server side) and `ToolExecutor` (client side, TypeScript)

The server's agent loop sends `ToolExecRequest` over the stream through a small server-side port:

```python
class ClientToolPort(Protocol):
    async def call(self, session_id: str, request: "ToolCall", *, timeout_s: float) -> "ToolOutcome": ...
```

The client implements the actual tools (`clients/xype-ai/src/tools/`):

```ts
export type ToolName =
  | "read_file" | "list_files" | "search" | "propose_edit" | "run_tests" | "run_command";

export interface ToolCall {
  readonly id: string;
  readonly taskId: string;
  readonly tool: ToolName;
  readonly args: Readonly<Record<string, unknown>>;
  readonly timeoutMs: number;
}

export type ToolOutcome =
  | { readonly status: "ok"; readonly output: string }
  | { readonly status: "error"; readonly message: string }
  | { readonly status: "denied_by_user" }
  | { readonly status: "timeout" };

export interface ApprovalPolicy {
  /** Local rules decide; the server hint `requiresApproval` can only make it stricter. */
  needsApproval(call: ToolCall, serverHint: boolean): boolean;
}

export interface ToolExecutor {
  readonly supported: ReadonlySet<ToolName>;
  execute(call: ToolCall, signal: AbortSignal): Promise<ToolOutcome>;
}
```

Client-side context-engine interfaces (TypeScript, Phase 2): `Chunker`, `CallGraph`, `MerkleTree`, `ClientCrypto`, `Scrubber`, `IdleScheduler`, `CompletionCache`. Their signatures are drafted in the Phase 2 spec.

## Interface checklist vs brief §2

| Brief interface | Defined above | Module |
|---|---|---|
| LLMProvider | ✅ | llm |
| Embedder | ✅ | embeddings |
| VectorStore | ✅ | retrieval |
| ChunkRepository | ✅ | retrieval |
| Cache | ✅ | cache |
| SemanticCache | ✅ | cache |
| TaskQueue | ✅ | tasks |
| TaskHandler | ✅ | tasks |
| ModelRouter | ✅ | router |
| PromptRegistry | ✅ | prompts |
| CryptoBox (concrete) | ✅ | security |
| SecretScrubber (concrete) | ✅ | security |
| KeyStore | ✅ | security |
| CredentialVerifier | ✅ | auth |
| MerkleIndex | ✅ | indexing |
| ConnectorClient (Company OS, Jira, templates) | ✅ | connectors |
| ToolExecutor | ✅ (TS) | clients/xype-ai |
| Clock | ✅ | tasks (shared) |
| CostScheduler | ✅ | tasks |
| Metrics | ✅ | observability |
