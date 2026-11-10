import asyncio
import copy
import json
import pickle
from collections.abc import AsyncIterator, Callable

import httpx
import pytest
from support.contracts import embedder as contract
from support.embeddings import FakeEmbedder

from common_lib.domain import ModelRef
from common_lib.embeddings import Embedder, EmbeddingError, EmbeddingUnavailableError
from common_lib.embeddings.ollama import OllamaEmbedder
from common_lib.secret_bytes import SecretBytes, SecretWipedError

DIMENSION = 8
MODEL = ModelRef("qwen3-embedding:0.6b", "test")

# mypy checks both adapters against the Protocol.
_fake: Embedder = FakeEmbedder()
_ollama: Embedder = OllamaEmbedder(base_url="http://ollama", model=MODEL)


type Make = Callable[..., OllamaEmbedder]


@pytest.fixture
async def make_ollama() -> AsyncIterator[Make]:
    """Embedders over httpx.MockTransport; the test owns and closes their clients."""
    clients: list[httpx.AsyncClient] = []

    def make(
        handler: Callable[[httpx.Request], httpx.Response], *, batch_size: int = 16
    ) -> OllamaEmbedder:
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="http://ollama", trust_env=False
        )
        clients.append(client)
        return OllamaEmbedder(
            model=MODEL, dimension=DIMENSION, client=client, batch_size=batch_size
        )

    yield make
    for client in clients:
        await client.aclose()


def fake_ollama(request: httpx.Request) -> httpx.Response:
    """Answers like Ollama: one vector per input, derived from the input."""
    inputs = json.loads(request.content)["input"]
    vectors = [[float((len(text) + i) % 7) for i in range(DIMENSION)] for text in inputs]
    return httpx.Response(200, json={"model": MODEL.model_id, "embeddings": vectors})


def unreachable(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("connect to http://ollama:11434 failed", request=request)


async def test_fake_embedder_meets_the_contract() -> None:
    embedder = FakeEmbedder(dimension=DIMENSION)
    await contract.assert_embeds_documents_and_wipes_them(embedder)
    await contract.assert_is_deterministic(embedder)
    await contract.assert_embeds_queries(embedder)
    await contract.assert_wipes_inputs_on_failure(FakeEmbedder(available=False))


async def test_ollama_embedder_meets_the_contract(make_ollama: Make) -> None:
    embedder = make_ollama(fake_ollama)
    await contract.assert_embeds_documents_and_wipes_them(embedder)
    await contract.assert_is_deterministic(embedder)
    await contract.assert_embeds_queries(embedder)
    await contract.assert_wipes_inputs_on_failure(make_ollama(unreachable))


async def test_ollama_request_follows_the_api_and_formats_queries(make_ollama: Make) -> None:
    seen: list[dict[str, object]] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        assert request.url.path == "/api/embed"
        return fake_ollama(request)

    embedder = make_ollama(record)
    await embedder.embed_query("add numbers", instruction="Find code")
    assert seen == [
        {
            "model": "qwen3-embedding:0.6b",
            "input": ["Instruct: Find code\nQuery:add numbers"],
            "dimensions": DIMENSION,
            "truncate": False,
        }
    ]


async def test_ollama_batches_documents(make_ollama: Make) -> None:
    sizes: list[int] = []

    def record(request: httpx.Request) -> httpx.Response:
        sizes.append(len(json.loads(request.content)["input"]))
        return fake_ollama(request)

    embedder = make_ollama(record, batch_size=2)
    vectors = await embedder.embed_documents([SecretBytes(b"x" * n) for n in range(5)])
    assert len(vectors) == 5
    assert sizes == [2, 2, 1]


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (httpx.Response(500, text="model 'def secret()' failed"), "answered 500"),
        (httpx.Response(200, text="not json"), "not valid"),
        (httpx.Response(200, json={"embeddings": [[1.0]]}), "8-dimensional"),
        (httpx.Response(200, json={"embeddings": []}), "number of vectors"),
        (httpx.Response(200, json={"embeddings": [["secret"] * 8]}), "not valid"),
        (httpx.Response(200, json={"embeddings": [[True] * 8]}), "not valid"),
        (httpx.Response(200, text='{"embeddings": [[NaN, 1, 1, 1, 1, 1, 1, 1]]}'), "not valid"),
        (httpx.Response(200, text='{"embeddings": [[1e400, 1, 1, 1, 1, 1, 1, 1]]}'), "not valid"),
        (
            httpx.Response(200, text='{"embeddings": [[' + "9" * 400 + ", 1, 1, 1, 1, 1, 1, 1]]}"),
            "not valid",
        ),
    ],
)
async def test_ollama_errors_carry_no_input_or_server_text(
    make_ollama: Make, response: httpx.Response, message: str
) -> None:
    embedder = make_ollama(lambda _request: response)
    inputs = [SecretBytes(b"def secret(): ...")]
    with pytest.raises(EmbeddingError, match=message) as raised:
        await embedder.embed_documents(inputs)
    assert "secret" not in str(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert inputs[0].wiped


async def test_unreachable_server_error_names_no_url(make_ollama: Make) -> None:
    with pytest.raises(EmbeddingUnavailableError) as raised:
        await make_ollama(unreachable).embed_query("q", instruction="i")
    assert "ollama:11434" not in str(raised.value)
    # The httpx error holds the request body (plaintext); it must not travel along.
    assert raised.value.__context__ is None


async def test_invalid_utf8_input_is_an_embedding_error_without_the_input(
    make_ollama: Make,
) -> None:
    inputs = [SecretBytes(b"\xffsecret-bytes")]
    with pytest.raises(EmbeddingError, match="UTF-8") as raised:
        await make_ollama(fake_ollama).embed_documents(inputs)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert inputs[0].wiped


def test_embedder_needs_exactly_one_of_base_url_and_client() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        OllamaEmbedder(model=MODEL)


@pytest.mark.parametrize(
    "client",
    [
        httpx.AsyncClient(base_url="http://ollama"),
        httpx.AsyncClient(base_url="http://ollama", trust_env=False, follow_redirects=True),
    ],
)
def test_injected_clients_must_not_use_proxies_or_redirects(client: httpx.AsyncClient) -> None:
    with pytest.raises(ValueError, match="trust_env"):
        OllamaEmbedder(model=MODEL, client=client)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:11434",
        "http://localhost:11434",
        "http://ollama:11434",
        "https://embed.example.com",
    ],
)
def test_local_http_and_any_https_are_allowed(url: str) -> None:
    OllamaEmbedder(model=MODEL, base_url=url)


async def test_embedder_closes_only_the_client_it_owns() -> None:
    owned = OllamaEmbedder(model=MODEL, base_url="http://ollama")
    await owned.aclose()
    shared = httpx.AsyncClient(base_url="http://ollama", trust_env=False)
    await OllamaEmbedder(model=MODEL, client=shared).aclose()
    assert not shared.is_closed
    await shared.aclose()


def test_secret_bytes_never_show_their_content() -> None:
    secret = SecretBytes(b"api_key = 'abc'")
    assert "abc" not in repr(secret)
    assert "abc" not in str(secret)
    assert secret.reveal() == b"api_key = 'abc'"
    secret.wipe()
    assert repr(secret) == "<SecretBytes wiped>"
    assert secret.is_zeroed()
    with pytest.raises(SecretWipedError):
        secret.reveal()
    for copy_it in (copy.copy, copy.deepcopy, pickle.dumps):
        with pytest.raises(TypeError):
            copy_it(SecretBytes(b"x"))


@pytest.mark.parametrize(
    ("model_id", "base_url"),
    [
        ("qwen3-embedding:0.6b", "https://ollama.com"),
        ("qwen3-embedding:0.6b", "https://api.ollama.com"),
        ("gpt-oss:120b-cloud", "http://ollama:11434"),
        ("qwen3-embedding:0.6b", "file:///etc/passwd"),
        ("GPT-OSS:120B-CLOUD", "http://ollama:11434"),
        ("qwen3-embedding:0.6b", "http://embed.example.com"),
    ],
)
def test_cloud_models_paid_hosts_and_other_schemes_are_refused(
    model_id: str, base_url: str
) -> None:
    with pytest.raises(ValueError, match=r"cloud|http"):
        OllamaEmbedder(model=ModelRef(model_id, "1"), base_url=base_url)


async def test_timeout_bounds_the_whole_request(make_ollama: Make) -> None:
    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return fake_ollama(request)

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(slow), base_url="http://ollama", trust_env=False
    )
    embedder = OllamaEmbedder(model=MODEL, dimension=DIMENSION, client=client, timeout_s=0.05)
    try:
        with pytest.raises(EmbeddingUnavailableError) as raised:
            await embedder.embed_query("q", instruction="i")
        assert raised.value.__context__ is None
    finally:
        await client.aclose()
