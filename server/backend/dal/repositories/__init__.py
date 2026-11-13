"""Registered repositories and their wrapped HMAC keys in Postgres (ADR-0016)."""

from backend.dal.repositories.store import SqlRepositoryStore

__all__ = ["SqlRepositoryStore"]
