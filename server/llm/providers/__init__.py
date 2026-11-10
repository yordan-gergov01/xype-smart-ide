"""The `LLMProvider` seam. Adapters: mock (here), ollama, vllm and openai_compatible
(OpenRouter and DeepSeek profiles, ADR-0005) later; resilience wrappers implement the
same Protocol (docs/interfaces.md)."""

from collections.abc import AsyncIterator
from typing import Protocol

from common_lib.llm import FimRequest, LLMRequest, LLMResponse, StreamEvent


class LLMProvider(Protocol):
    name: str

    async def generate(self, request: LLMRequest, *, timeout_s: float) -> LLMResponse:
        """One complete answer. Raises `LLMTimeoutError` or `LLMUnavailableError`."""
        ...

    def stream(self, request: LLMRequest, *, timeout_s: float) -> AsyncIterator[StreamEvent]:
        """The answer as deltas; the last event carries usage and the finish reason."""
        ...

    async def complete_fim(self, request: FimRequest, *, timeout_s: float) -> LLMResponse:
        """Fill-in-the-middle completion between prefix and suffix."""
        ...

    async def health(self) -> bool:
        """Whether the provider can answer now."""
        ...
