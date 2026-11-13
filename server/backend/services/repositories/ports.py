"""What repository registration needs from its stores (rule 03)."""

import uuid
from typing import Protocol

from common_lib.repositories import NewRepository


class RepositoryStore(Protocol):
    async def create(self, repository: NewRepository) -> None:
        """Writes the repository and its key in one transaction. Raises
        `DuplicateRepositoryError` when the team already has a repository of this name."""
        ...


class TeamDirectory(Protocol):
    """Satisfied by the auth feature's `UserStore`."""

    async def find_team(self, name: str) -> uuid.UUID | None: ...
