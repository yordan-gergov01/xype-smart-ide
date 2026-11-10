"""The `Embedder` contract: the fake, OllamaEmbedder and (hosted) TEI all run it."""

import math

import pytest

from common_lib.embeddings import Embedder, EmbeddingError
from common_lib.secret_bytes import SecretBytes

INSTRUCTION = "Given a question about code, retrieve the code that answers it"
CHUNKS = (b"def add(a, b):\n    return a + b\n", b"class Invoice:\n    total: int\n")


async def assert_embeds_documents_and_wipes_them(embedder: Embedder) -> None:
    inputs = [SecretBytes(c) for c in CHUNKS]
    vectors = await embedder.embed_documents(inputs)
    assert len(vectors) == len(CHUNKS)
    assert all(len(v) == embedder.dimension for v in vectors)
    assert all(math.isfinite(x) for v in vectors for x in v)
    assert all(i.wiped for i in inputs)
    assert await embedder.embed_documents([]) == []


async def assert_is_deterministic(embedder: Embedder) -> None:
    first = await embedder.embed_documents([SecretBytes(CHUNKS[0])])
    second = await embedder.embed_documents([SecretBytes(CHUNKS[0])])
    assert first[0] == pytest.approx(second[0], abs=1e-4)


async def assert_embeds_queries(embedder: Embedder) -> None:
    vector = await embedder.embed_query("How are two numbers added?", instruction=INSTRUCTION)
    assert len(vector) == embedder.dimension


async def assert_wipes_inputs_on_failure(failing: Embedder) -> None:
    """`failing` cannot reach its model server."""
    inputs = [SecretBytes(c) for c in CHUNKS]
    with pytest.raises(EmbeddingError):
        await failing.embed_documents(inputs)
    assert all(i.wiped for i in inputs)
