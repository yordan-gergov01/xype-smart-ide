"""`FakeEmbedder`: deterministic hash-based vectors, unit length, no model server."""

import hashlib
import math
from collections.abc import Sequence

from common_lib.domain import ModelRef
from common_lib.embeddings import EmbeddingUnavailableError, query_input
from common_lib.secret_bytes import SecretBytes


class FakeEmbedder:
    def __init__(self, *, dimension: int = 1024, available: bool = True) -> None:
        self.model = ModelRef("fake-embedder", "1")
        self.dimension = dimension
        self._available = available

    async def embed_documents(self, texts: Sequence[SecretBytes]) -> Sequence[Sequence[float]]:
        try:
            if not self._available:
                raise EmbeddingUnavailableError
            return [self._vector(t.reveal()) for t in texts]
        finally:
            for text in texts:
                text.wipe()

    async def embed_query(self, text: str, *, instruction: str) -> Sequence[float]:
        if not self._available:
            raise EmbeddingUnavailableError
        return self._vector(query_input(text, instruction).encode())

    def _vector(self, data: bytes) -> list[float]:
        raw: list[float] = []
        counter = 0
        while len(raw) < self.dimension:
            block = hashlib.sha256(data + counter.to_bytes(4, "big")).digest()
            raw.extend((b - 127.5) / 127.5 for b in block)
            counter += 1
        raw = raw[: self.dimension]
        norm = math.sqrt(sum(x * x for x in raw))
        return [x / norm for x in raw]
