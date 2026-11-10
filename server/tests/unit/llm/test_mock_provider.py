import json

import pytest
from support.contracts import llm as contract

from common_lib.llm import (
    FimRequest,
    LLMRequest,
    LLMResponse,
    LLMUnavailableError,
    Message,
    StreamEvent,
    TokenUsage,
    ToolCall,
    ToolSpec,
)
from llm.providers import LLMProvider
from llm.providers.mock import MockLLMProvider, MockScript

# mypy checks the mock against the Protocol.
_conforms: LLMProvider = MockLLMProvider()


async def test_mock_meets_the_provider_contract() -> None:
    provider = MockLLMProvider()
    await contract.assert_generates(provider)
    await contract.assert_streams_the_same_answer(provider)
    await contract.assert_completes_fim(provider)
    await contract.assert_reports_health(provider)
    await contract.assert_times_out(MockLLMProvider(MockScript(latency_s=1)))


async def test_answers_are_deterministic_and_never_echo_the_request() -> None:
    provider = MockLLMProvider()
    secret = "def charge(card): return gateway.pay(card)"
    first = await provider.generate(contract.chat_request(secret), timeout_s=1)
    second = await provider.generate(contract.chat_request(secret), timeout_s=1)
    other = await provider.generate(contract.chat_request("something else"), timeout_s=1)
    assert first.text == second.text
    assert first.text != other.text
    assert secret not in first.text
    assert first.usage.input_cached_tokens == 0


async def test_scripted_failures_and_health() -> None:
    failing = MockLLMProvider(MockScript(error=LLMUnavailableError, healthy=False))
    with pytest.raises(LLMUnavailableError):
        await failing.generate(contract.chat_request(), timeout_s=1)
    assert await failing.health() is False


async def test_tool_calls_when_tools_are_offered() -> None:
    call = ToolCall(id="call-1", name="read_file", arguments='{"file_id": "a"}')
    provider = MockLLMProvider(MockScript(tool_call=call))
    request = LLMRequest(
        model=contract.MODEL,
        messages=(Message("user", "Read it."),),
        max_tokens=16,
        tools=(ToolSpec("read_file", "Read a file", {"type": "object"}),),
    )
    response = await provider.generate(request, timeout_s=1)
    assert response.finish_reason == "tool_calls"
    assert response.tool_calls == (call,)
    events = [e async for e in provider.stream(request, timeout_s=1)]
    assert [e.tool_call for e in events if e.tool_call] == [call]


async def test_json_replies_for_a_response_schema() -> None:
    provider = MockLLMProvider(MockScript(json_reply={"verdict": "ok", "score": 1}))
    request = LLMRequest(
        model=contract.MODEL,
        messages=(Message("user", "Judge it."),),
        max_tokens=16,
        response_schema={"type": "object"},
    )
    response = await provider.generate(request, timeout_s=1)
    assert json.loads(response.text) == {"verdict": "ok", "score": 1}


async def test_scripted_latency_is_waited_for() -> None:
    response = await MockLLMProvider(MockScript(latency_s=0.01)).generate(
        contract.chat_request(), timeout_s=1
    )
    assert response.finish_reason == "stop"


async def test_different_splits_of_the_same_text_get_different_answers() -> None:
    provider = MockLLMProvider()

    def request(*contents: str) -> LLMRequest:
        return LLMRequest(
            model=contract.MODEL,
            messages=tuple(Message("user", c) for c in contents),
            max_tokens=8,
        )

    first = await provider.generate(request("ab", "c"), timeout_s=1)
    second = await provider.generate(request("a", "bc"), timeout_s=1)
    assert first.text != second.text


def test_reprs_show_no_content() -> None:
    secret = "SECRET-CONTENT"
    call = ToolCall(id="c1", name="read_file", arguments=secret)
    values = (
        Message("user", secret),
        call,
        LLMRequest(model=contract.MODEL, messages=(Message("user", secret),), max_tokens=1),
        FimRequest(contract.MODEL, prefix=secret, suffix=secret, max_tokens=1),
        LLMResponse(
            text=secret,
            usage=TokenUsage(1, 0, 1),
            model=contract.MODEL,
            finish_reason="stop",
            tool_calls=(call,),
        ),
        StreamEvent(delta=secret, tool_call=call),
    )
    for value in values:
        assert secret not in repr(value), type(value).__name__
