"""`MockLLMProvider`: deterministic and scriptable, free, offline (brief §1, rule 07).

The default for development, tests, CI and evals. Same request, same answer: the
reply is derived from a hash of the request, never from randomness, and never
repeats the request's content. A `MockScript` sets latency, failures, tool calls,
JSON replies and FIM output.
"""

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field

from common_lib.domain import ModelRef
from common_lib.llm import (
    FimRequest,
    LLMError,
    LLMRequest,
    LLMResponse,
    LLMTimeoutError,
    StreamEvent,
    TokenUsage,
    ToolCall,
)

CHARS_PER_TOKEN = 4


@dataclass(frozen=True, slots=True)
class MockScript:
    """How the mock behaves. The defaults answer quickly and successfully."""

    latency_s: float = 0.0
    # Raised (a fresh instance per call) instead of answering, e.g. LLMUnavailableError.
    error: type[LLMError] | None = None
    healthy: bool = True
    # Returned for requests that offer tools.
    tool_call: ToolCall | None = None
    # Returned (as JSON text) for requests with a response schema.
    json_reply: Mapping[str, object] = field(default_factory=dict)
    fim_reply: str = "pass"
    words_per_delta: int = 2


def _tokens(text: str) -> int:
    return max(1, len(text) // CHARS_PER_TOKEN) if text else 0


class MockLLMProvider:
    name = "mock"

    def __init__(self, script: MockScript | None = None) -> None:
        self._script = script or MockScript()

    async def generate(self, request: LLMRequest, *, timeout_s: float) -> LLMResponse:
        await self._wait(timeout_s)
        usage = TokenUsage(
            input_tokens=sum(_tokens(m.content) for m in request.messages),
            input_cached_tokens=0,
            output_tokens=0,
        )
        if request.tools and self._script.tool_call is not None:
            return LLMResponse(
                text="",
                usage=usage,
                model=request.model,
                finish_reason="tool_calls",
                tool_calls=(self._script.tool_call,),
            )
        text = self._reply(request)
        usage = TokenUsage(usage.input_tokens, 0, _tokens(text))
        return LLMResponse(text=text, usage=usage, model=request.model, finish_reason="stop")

    async def stream(self, request: LLMRequest, *, timeout_s: float) -> AsyncIterator[StreamEvent]:
        response = await self.generate(request, timeout_s=timeout_s)
        words = response.text.split(" ")
        size = self._script.words_per_delta
        for start in range(0, len(words), size):
            piece = " ".join(words[start : start + size])
            yield StreamEvent(delta=piece if start == 0 else f" {piece}")
        for call in response.tool_calls:
            yield StreamEvent(delta="", tool_call=call)
        yield StreamEvent(delta="", usage=response.usage, finish_reason=response.finish_reason)

    async def complete_fim(self, request: FimRequest, *, timeout_s: float) -> LLMResponse:
        await self._wait(timeout_s)
        text = self._script.fim_reply
        usage = TokenUsage(_tokens(request.prefix) + _tokens(request.suffix), 0, _tokens(text))
        return LLMResponse(text=text, usage=usage, model=request.model, finish_reason="stop")

    async def health(self) -> bool:
        return self._script.healthy

    async def _wait(self, timeout_s: float) -> None:
        if self._script.latency_s > timeout_s:
            await asyncio.sleep(timeout_s)
            raise LLMTimeoutError
        if self._script.latency_s:
            await asyncio.sleep(self._script.latency_s)
        if self._script.error is not None:
            raise self._script.error

    def _reply(self, request: LLMRequest) -> str:
        if request.response_schema is not None:
            return json.dumps(dict(self._script.json_reply), sort_keys=True)
        return f"[{_label(request.model)}] Mock answer {_fingerprint(request)}."


def _label(model: ModelRef) -> str:
    return f"{model.model_id}@{model.model_version}"


def _fingerprint(request: LLMRequest) -> str:
    """A hash over every field that shapes the answer, length-prefixed so that
    different splits of the same text never collide."""
    parts = [m.role + "\0" + m.content for m in request.messages]
    parts += [
        t.name + "\0" + json.dumps(dict(t.json_schema), sort_keys=True) for t in request.tools
    ]
    parts.append(json.dumps(request.response_schema, sort_keys=True))
    digest = hashlib.sha256()
    for part in parts:
        data = part.encode()
        digest.update(len(data).to_bytes(8, "big") + data)
    return digest.hexdigest()[:12]
