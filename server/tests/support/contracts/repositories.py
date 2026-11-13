"""Contract of `RepositoryStore` and `RepositoryKeySource`: the fake (unit) and the
Postgres adapter (integration) both run it (rule 03)."""

import os
import uuid
from collections.abc import Awaitable, Callable
from typing import Protocol

import pytest

from backend.services.auth import RepositoryKeySource
from backend.services.repositories import RepositoryStore
from common_lib.repositories import DuplicateRepositoryError, NewRepository
from common_lib.security import WrappedKey


class Store(RepositoryStore, RepositoryKeySource, Protocol):
    """One adapter serves both seams."""


# Marks a repository's key retired in the store under test (rotation is not built yet).
type Retire = Callable[[uuid.UUID], Awaitable[None]]


def new_repository(team_id: uuid.UUID, name: str) -> NewRepository:
    return NewRepository(
        repository_id=uuid.uuid4(),
        team_id=team_id,
        name=name,
        remote_url="https://git.example.test/web/app.git",
        is_template=False,
        hmac_key_id=str(uuid.uuid4()),
        wrapped_key=WrappedKey("kek-1", os.urandom(12), os.urandom(48)),
    )


async def check_keys_round_trip_for_the_team_only(
    store: Store, teams: tuple[uuid.UUID, uuid.UUID]
) -> None:
    mine, theirs = teams
    first, second = new_repository(mine, "a"), new_repository(mine, "b")
    other = new_repository(theirs, "a")
    for repository in (first, second, other):
        await store.create(repository)
    unknown = uuid.uuid4()
    wanted = {first.repository_id, second.repository_id, other.repository_id, unknown}
    keys = await store.active_keys(mine, wanted)
    got = {(k.repository_id, k.hmac_key_id, k.wrapped_key) for k in keys}
    assert got == {(r.repository_id, r.hmac_key_id, r.wrapped_key) for r in (first, second)}
    # Another team's id and an unknown one are left out the same way.
    assert await store.active_keys(mine, {other.repository_id}) == ()
    assert await store.active_keys(mine, {unknown}) == ()
    assert await store.active_keys(theirs, {first.repository_id}) == ()
    assert await store.active_keys(mine, set()) == ()


async def check_a_name_is_unique_per_team(store: Store, teams: tuple[uuid.UUID, uuid.UUID]) -> None:
    mine, theirs = teams
    await store.create(new_repository(mine, "same"))
    await store.create(new_repository(theirs, "same"))
    with pytest.raises(DuplicateRepositoryError):
        await store.create(new_repository(mine, "same"))


async def check_a_retired_key_is_not_issued(
    store: Store, retire: Retire, team_id: uuid.UUID
) -> None:
    kept, retired = new_repository(team_id, "kept"), new_repository(team_id, "retired")
    for repository in (kept, retired):
        await store.create(repository)
    await retire(retired.repository_id)
    keys = await store.active_keys(team_id, {kept.repository_id, retired.repository_id})
    assert [k.repository_id for k in keys] == [kept.repository_id]
