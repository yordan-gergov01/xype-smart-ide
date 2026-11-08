"""Domain value types shared by every service (see docs/interfaces.md)."""

import re
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from common_lib.errors import InvalidValueError

type TaskType = Literal[
    "completion", "chat", "refactor", "tests", "docs", "lint", "implement_ticket"
]

# HMAC-SHA256 as lowercase hex: the only form file ids and chunk hashes take on the server.
_HMAC_HEX = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class RequestContext:
    """Who is asking and why; propagated to every call, log and metric."""

    user_id: UUID
    team_id: UUID
    project_id: UUID | None
    correlation_id: str
    task_type: TaskType

    def __post_init__(self) -> None:
        if not self.correlation_id:
            raise InvalidValueError("correlation_id must not be empty")


@dataclass(frozen=True, slots=True)
class ChunkRef:
    """Obfuscated reference to a code region; never carries file names or code."""

    repository_id: UUID
    obfuscated_file_id: str
    line_start: int
    line_end: int
    chunk_hash: str

    def __post_init__(self) -> None:
        if self.line_start < 1 or self.line_end < self.line_start:
            raise InvalidValueError(f"invalid line range {self.line_start}-{self.line_end}")
        for name in ("obfuscated_file_id", "chunk_hash"):
            if not _HMAC_HEX.fullmatch(getattr(self, name)):
                raise InvalidValueError(f"{name} must be a 64-char lowercase hex HMAC")


@dataclass(frozen=True, slots=True)
class ModelRef:
    """A model identity; the version is part of every cache key."""

    model_id: str
    model_version: str

    def __post_init__(self) -> None:
        if not self.model_id or not self.model_version:
            raise InvalidValueError("model_id and model_version must not be empty")
