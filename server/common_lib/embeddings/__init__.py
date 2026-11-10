"""The `Embedder` seam (docs/interfaces.md, ADR-0002).

Shared by the backend (query embeddings on the chat path) and the indexer (document
embeddings). Embedding is stateless: inputs are wiped before `embed_documents`
returns, whether it succeeds or fails (brief §3.4.7).
"""

from collections.abc import Sequence
from typing import Protocol

from common_lib.domain import ModelRef
from common_lib.errors import XypeError
from common_lib.secret_bytes import SecretBytes


class EmbeddingError(XypeError):
    """The model server answered something unusable. Never carries input text."""


class EmbeddingUnavailableError(EmbeddingError):
    """The model server cannot be reached or did not answer in time."""


class Embedder(Protocol):
    # Part of every embedding-cache key; includes the quantisation.
    model: ModelRef
    dimension: int

    async def embed_documents(self, texts: Sequence[SecretBytes]) -> Sequence[Sequence[float]]:
        """One vector per input, in order. Wipes every input before returning."""
        ...

    async def embed_query(self, text: str, *, instruction: str) -> Sequence[float]:
        """A query vector; `instruction` is the retrieval task (a versioned prompt).
        The query is content but arrives as `str`, which cannot be wiped."""
        ...


def query_input(text: str, instruction: str) -> str:
    """Qwen3-Embedding's query format; documents are embedded without it (ADR-0002)."""
    return f"Instruct: {instruction}\nQuery:{text}"


__all__ = [
    "Embedder",
    "EmbeddingError",
    "EmbeddingUnavailableError",
    "query_input",
]
