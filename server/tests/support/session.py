"""`FakeRepositoryAccess` and the `RepositoryAccess` contract (rule 03): the fake and
`SqlRepositoryAccess` run the same checks."""

import uuid
from collections.abc import Awaitable, Callable, Collection

from backend.services.session import RepositoryAccess


class FakeRepositoryAccess:
    """Repositories by team, as registered."""

    def __init__(self) -> None:
        self.teams: dict[uuid.UUID, set[uuid.UUID]] = {}

    def register(self, team_id: uuid.UUID) -> uuid.UUID:
        repository_id = uuid.uuid4()
        self.teams.setdefault(team_id, set()).add(repository_id)
        return repository_id

    async def accessible(
        self, team_id: uuid.UUID, repository_ids: Collection[uuid.UUID]
    ) -> frozenset[uuid.UUID]:
        return frozenset(self.teams.get(team_id, set()) & set(repository_ids))


# Registers a repository for a team in the store under test; returns its id.
type Register = Callable[[uuid.UUID], Awaitable[uuid.UUID]]


async def assert_only_the_teams_repositories(
    access: RepositoryAccess, register: Register, teams: tuple[uuid.UUID, uuid.UUID]
) -> None:
    mine, theirs = teams
    own = {await register(mine), await register(mine)}
    other = await register(theirs)
    unknown = uuid.uuid4()
    assert await access.accessible(mine, own) == own
    assert await access.accessible(mine, {*own, other, unknown}) == own
    assert await access.accessible(theirs, own) == frozenset()
    assert await access.accessible(mine, set()) == frozenset()
