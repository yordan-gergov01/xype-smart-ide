"""The session feature's seams. The adapter lives in `backend.dal.session`; the fake and
the shared contract suite in tests/support (rule 03)."""

import uuid
from collections.abc import Collection
from typing import Protocol


class RepositoryAccess(Protocol):
    """Which repositories a team may use. Raises `DependencyUnavailableError` when the
    database is unreachable."""

    async def accessible(
        self, team_id: uuid.UUID, repository_ids: Collection[uuid.UUID]
    ) -> frozenset[uuid.UUID]:
        """The subset of `repository_ids` registered to `team_id`."""
        ...
