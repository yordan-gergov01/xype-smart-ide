"""Value types and errors of the LLM seam (docs/interfaces.md, "llm").

Shared by the backend (prompts, router) and the LLM worker, which owns the
`LLMProvider` Protocol and its adapters. Every `str` that carries a prompt, code or
an answer is content: it is never logged, traced or stored in clear, and is left out
of every `repr` (`field(repr=False)`), so an f-string or a log of a request shows
ids and sizes only.

Adapters raise their typed errors *after* the handler that caught a client error,
never inside it, so the original (which may hold the request body) is not kept as
`__context__` (see common_lib/embeddings/ollama.py).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

from common_lib.domain import ModelRef
from common_lib.errors import XypeError

type Role = Literal["system", "user", "assistant", "tool"]
type FinishReason = Literal["stop", "length", "tool_calls", "error"]


@dataclass(frozen=True, slots=True)
class Message:
    role: Role
    content: str = field(repr=False)
    tool_call_id: str | None = None


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    json_schema: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class ToolCall:
    """A model's request to run a tool. `arguments` is content (JSON text)."""

    id: str
    name: str
    arguments: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class LLMRequest:
    model: ModelRef
    # Stable prefix first, volatile suffix last (provider prefix caching, brief §5).
    messages: Sequence[Message] = field(repr=False)
    max_tokens: int
    temperature: float = 0.0
    tools: Sequence[ToolSpec] = ()
    response_schema: Mapping[str, object] | None = None
    stop: Sequence[str] = ()


@dataclass(frozen=True, slots=True)
class FimRequest:
    model: ModelRef
    prefix: str = field(repr=False)
    suffix: str = field(repr=False)
    max_tokens: int
    stop: Sequence[str] = ()


@dataclass(frozen=True, slots=True)
class TokenUsage:
    input_tokens: int
    input_cached_tokens: int
    output_tokens: int


@dataclass(frozen=True, slots=True)
class LLMResponse:
    text: str = field(repr=False)
    usage: TokenUsage
    model: ModelRef
    finish_reason: FinishReason
    tool_calls: Sequence[ToolCall] = field(default_factory=tuple, repr=False)


@dataclass(frozen=True, slots=True)
class StreamEvent:
    delta: str = field(repr=False)
    tool_call: ToolCall | None = field(default=None, repr=False)
    # Present on the final event only.
    usage: TokenUsage | None = None
    finish_reason: FinishReason | None = None


class LLMError(XypeError):
    """Base of every LLM provider error. Messages never contain request content."""


class LLMTimeoutError(LLMError):
    """The provider did not answer within the timeout."""


class LLMUnavailableError(LLMError):
    """The provider cannot be reached or refused the request (rate limit, 5xx)."""
