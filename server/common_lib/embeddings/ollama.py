"""`OllamaEmbedder`: Qwen3-Embedding-0.6B served by the local Ollama (ADR-0002).

POST {base_url}/api/embed with {model, input, dimensions, truncate}. Inputs are never
truncated silently: a chunk longer than the context is an error, so its vector is
never built from part of it.

Errors: every failure becomes an `EmbeddingError` with a fixed message, raised
*after* the handler that caught the original. Raising inside it would keep the
original as `__context__`, and those carry plaintext (`httpx` request bodies,
`JSONDecodeError.doc`, `UnicodeDecodeError.object`).

Transport: the client ignores proxy and TLS environment variables and never follows
redirects, so code goes only to the configured server. Plain http is accepted for
the compose-internal `models` network only (ADR-0002); Ollama cloud models and
ollama.com are refused.
"""

import asyncio
import ipaddress
import math
from collections.abc import Sequence
from urllib.parse import urlsplit

import httpx

from common_lib.domain import ModelRef
from common_lib.embeddings import EmbeddingError, EmbeddingUnavailableError, query_input
from common_lib.secret_bytes import SecretBytes

EMBED_PATH = "/api/embed"
# Ollama's cloud models are paid remote inference (ADR-0034, tools/paid_api_hosts.txt).
CLOUD_HOST = "ollama.com"


class OllamaEmbedder:
    def __init__(
        self,
        *,
        model: ModelRef,
        base_url: str | None = None,
        dimension: int = 1024,
        timeout_s: float = 60.0,
        batch_size: int = 16,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        """Give `base_url` (the embedder owns and closes its client) or an existing
        `client` configured by the caller, who also closes it. `timeout_s` bounds
        every request in total, whichever is given."""
        if (base_url is None) == (client is None):
            raise ValueError("give exactly one of base_url and client")
        if client is not None and (client.trust_env or client.follow_redirects):
            raise ValueError("the client must use trust_env=False and follow_redirects=False")
        _refuse_cloud(model, base_url or str(client.base_url if client else ""))
        self.model = model
        self.dimension = dimension
        self._batch_size = batch_size
        self._timeout_s = timeout_s
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=base_url or "", timeout=timeout_s, trust_env=False, follow_redirects=False
        )

    async def embed_documents(self, texts: Sequence[SecretBytes]) -> Sequence[Sequence[float]]:
        try:
            vectors: list[list[float]] = []
            for start in range(0, len(texts), self._batch_size):
                vectors.extend(await self._embed(_decode(texts[start : start + self._batch_size])))
            return vectors
        finally:
            for text in texts:
                text.wipe()

    async def embed_query(self, text: str, *, instruction: str) -> Sequence[float]:
        (vector,) = await self._embed([query_input(text, instruction)])
        return vector

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _embed(self, inputs: list[str]) -> list[list[float]]:
        if not inputs:
            return []
        body = {
            "model": self.model.model_id,
            "input": inputs,
            "dimensions": self.dimension,
            "truncate": False,
        }
        response: httpx.Response | None
        try:
            async with asyncio.timeout(self._timeout_s):
                response = await self._client.post(EMBED_PATH, json=body)
        except (httpx.HTTPError, TimeoutError):
            response = None
        if response is None:
            raise EmbeddingUnavailableError
        if response.status_code != httpx.codes.OK:
            raise EmbeddingError(f"embedding server answered {response.status_code}")
        return self._vectors(response, expected=len(inputs))

    def _vectors(self, response: httpx.Response, *, expected: int) -> list[list[float]]:
        vectors: object
        try:
            vectors = response.json()["embeddings"]
        except (ValueError, KeyError, TypeError):
            vectors = None
        if not isinstance(vectors, list):
            raise EmbeddingError("embedding response is not valid")
        if len(vectors) != expected:
            raise EmbeddingError("embedding response has the wrong number of vectors")
        if any(not isinstance(v, list) or len(v) != self.dimension for v in vectors):
            raise EmbeddingError(f"embedding response is not {self.dimension}-dimensional")
        converted = _finite_floats(vectors)
        if converted is None:
            raise EmbeddingError("embedding response is not valid")
        return converted


def _decode(texts: Sequence[SecretBytes]) -> list[str]:
    decoded: list[str] | None
    try:
        decoded = [t.reveal().decode("utf-8") for t in texts]
    except UnicodeDecodeError:
        decoded = None
    if decoded is None:
        raise EmbeddingError("embedding input is not valid UTF-8")
    return decoded


def _finite_floats(vectors: list[list[object]]) -> list[list[float]] | None:
    """Every value as a finite float, or None (bools, NaN, infinities, overflow)."""
    result: list[list[float]] = []
    for vector in vectors:
        row: list[float] = []
        for value in vector:
            if not isinstance(value, int | float) or isinstance(value, bool):
                return None
            try:
                number = float(value)
            except OverflowError:
                return None
            if not math.isfinite(number):
                return None
            row.append(number)
        result.append(row)
    return result


def _refuse_cloud(model: ModelRef, base_url: str) -> None:
    """https anywhere; plain http only to loopback or a compose service name (no dot),
    so plaintext chunks never cross a real network unencrypted. Never cloud models."""
    parts = urlsplit(base_url)
    host = (parts.hostname or "").lower()
    if parts.scheme not in {"http", "https"}:
        raise ValueError("the embedding server URL must be http or https")
    if parts.scheme == "http" and not _is_local(host):
        raise ValueError("plain http is allowed only to loopback or a compose service")
    if host == CLOUD_HOST or host.endswith(f".{CLOUD_HOST}") or "cloud" in model.model_id.lower():
        raise ValueError("Ollama cloud models and ollama.com are paid remote inference")


def _is_local(host: str) -> bool:
    if host == "localhost" or ("." not in host and ":" not in host and host):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False
