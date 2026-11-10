"""The `LLMProvider` contract. Every adapter (mock now; ollama, vllm and
openai_compatible later, the paid ones against recorded fixtures only) runs it."""

import pytest

from common_lib.domain import ModelRef
from common_lib.llm import FimRequest, LLMRequest, LLMTimeoutError, Message
from llm.providers import LLMProvider

MODEL = ModelRef("contract-model", "1")


def chat_request(text: str = "Explain this function.") -> LLMRequest:
    return LLMRequest(
        model=MODEL,
        messages=(Message("system", "You are a code assistant."), Message("user", text)),
        max_tokens=64,
    )


async def assert_generates(provider: LLMProvider) -> None:
    response = await provider.generate(chat_request(), timeout_s=30)
    assert response.model == MODEL
    assert response.text
    assert response.finish_reason in {"stop", "length"}
    assert response.usage.input_tokens > 0
    assert response.usage.output_tokens >= 0


async def assert_streams_the_same_answer(provider: LLMProvider) -> None:
    whole = await provider.generate(chat_request(), timeout_s=30)
    events = [e async for e in provider.stream(chat_request(), timeout_s=30)]
    assert "".join(e.delta for e in events) == whole.text
    assert events[-1].usage is not None
    assert events[-1].finish_reason == whole.finish_reason
    assert all(e.usage is None for e in events[:-1])


async def assert_completes_fim(provider: LLMProvider) -> None:
    request = FimRequest(MODEL, prefix="def add(a, b):\n    return ", suffix="\n", max_tokens=16)
    response = await provider.complete_fim(request, timeout_s=30)
    assert response.model == MODEL
    assert response.finish_reason in {"stop", "length"}


async def assert_times_out(slow_provider: LLMProvider) -> None:
    """`slow_provider` answers slower than 0.05 s."""
    with pytest.raises(LLMTimeoutError):
        await slow_provider.generate(chat_request(), timeout_s=0.05)


async def assert_reports_health(provider: LLMProvider) -> None:
    assert isinstance(await provider.health(), bool)
