"""The vector store in Postgres with pgvector (ADR-0020)."""

from backend.dal.retrieval.vector_store import PgVectorStore, search_query

__all__ = ["PgVectorStore", "search_query"]
