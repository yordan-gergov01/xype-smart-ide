"""SQLAlchemy 2 models: the single source of the database schema (migrations are
generated from them and reviewed by hand). Only the backend and migrations import
this package; workers never do (no database access, ADR-0006)."""

from common_lib.models.base import Base
from common_lib.models.chat import ChatMessage, ChatSession
from common_lib.models.index import EMBEDDING_DIMENSION, ChunkEdge, CodeChunk, MerkleNode
from common_lib.models.llm import LlmModel, UserPreferences
from common_lib.models.quality import CostLedgerEntry, EvalResult, EvalRun, Feedback
from common_lib.models.tasks import AgentTask, TaskEvent
from common_lib.models.tenancy import (
    Project,
    RefreshToken,
    Repository,
    RepositoryKey,
    Team,
    User,
)

__all__ = [
    "EMBEDDING_DIMENSION",
    "AgentTask",
    "Base",
    "ChatMessage",
    "ChatSession",
    "ChunkEdge",
    "CodeChunk",
    "CostLedgerEntry",
    "EvalResult",
    "EvalRun",
    "Feedback",
    "LlmModel",
    "MerkleNode",
    "Project",
    "RefreshToken",
    "Repository",
    "RepositoryKey",
    "TaskEvent",
    "Team",
    "User",
    "UserPreferences",
]
