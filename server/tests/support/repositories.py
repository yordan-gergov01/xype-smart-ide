"""`FakeRepositoryStore`: registered repositories and their wrapped keys in memory, with
the checks `SqlRepositoryStore` makes (support/contracts/repositories.py)."""

import uuid
from collections.abc import Collection, Sequence

from common_lib.repositories import DuplicateRepositoryError, NewRepository, StoredRepositoryKey


class FakeRepositoryStore:
    def __init__(self) -> None:
        self.repositories: dict[uuid.UUID, NewRepository] = {}
        self.retired: set[uuid.UUID] = set()
        self.unavailable: Exception | None = None

    async def create(self, repository: NewRepository) -> None:
        taken = any(
            r.team_id == repository.team_id and r.name == repository.name
            for r in self.repositories.values()
        )
        if taken or repository.repository_id in self.repositories:
            raise DuplicateRepositoryError("already exists")
        self.repositories[repository.repository_id] = repository

    async def active_keys(
        self, team_id: uuid.UUID, repository_ids: Collection[uuid.UUID]
    ) -> Sequence[StoredRepositoryKey]:
        if self.unavailable is not None:
            raise self.unavailable
        return tuple(
            StoredRepositoryKey(r.repository_id, r.hmac_key_id, r.wrapped_key)
            for r in sorted(self.repositories.values(), key=lambda r: r.repository_id)
            if r.team_id == team_id
            and r.repository_id in repository_ids
            and r.repository_id not in self.retired
        )
