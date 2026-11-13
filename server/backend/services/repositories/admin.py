"""Repository registration, by an admin (ADR-0016): the CLI now, the admin dashboard
(Phase 7) later, both through this service. Registration creates the repository's
random HMAC key; the server never computes an identifier with it."""

import uuid
from dataclasses import dataclass

import structlog

from backend.services.repositories.ports import RepositoryStore, TeamDirectory
from backend.services.repositories.urls import without_credentials
from common_lib.clock import Clock
from common_lib.domain import uuid7
from common_lib.errors import InvalidValueError
from common_lib.repositories import NewRepository
from common_lib.security import Kek, KekPurpose, new_key, repository_key_aad, wrap_key

logger = structlog.get_logger(__name__)

MAX_NAME_LENGTH = 200


class UnknownTeamError(InvalidValueError):
    """No team has this name."""


@dataclass(frozen=True, slots=True)
class RegisteredRepository:
    repository_id: uuid.UUID
    hmac_key_id: str


class RepositoryAdmin:
    def __init__(
        self, store: RepositoryStore, teams: TeamDirectory, *, kek: Kek, clock: Clock
    ) -> None:
        self._store = store
        self._teams = teams
        self._kek = kek
        self._clock = clock

    async def register(
        self, *, team: str, name: str, remote_url: str | None = None, is_template: bool = False
    ) -> RegisteredRepository:
        """Raises `UnknownTeamError`, `InvalidValueError` for a bad name or URL, and
        `DuplicateRepositoryError` when the team already has a repository of this name."""
        name = name.strip()
        if not name or len(name) > MAX_NAME_LENGTH:
            raise InvalidValueError("a repository name has 1 to 200 characters")
        url = without_credentials(remote_url) if remote_url is not None else None
        team_id = await self._teams.find_team(team)
        if team_id is None:
            raise UnknownTeamError("unknown team")
        now = self._clock.now()
        repository_id, hmac_key_id = uuid7(now), str(uuid7(now))
        key = new_key()
        try:
            aad = repository_key_aad(str(repository_id), hmac_key_id, self._kek.kek_id)
            wrapped = wrap_key(self._kek, KekPurpose.REPO_KEYS, key, aad)
        finally:
            key.wipe()
        await self._store.create(
            NewRepository(
                repository_id=repository_id,
                team_id=team_id,
                name=name,
                remote_url=url,
                is_template=is_template,
                hmac_key_id=hmac_key_id,
                wrapped_key=wrapped,
            )
        )
        logger.info(
            "repositories.registered", repository_id=str(repository_id), team_id=str(team_id)
        )
        return RegisteredRepository(repository_id, hmac_key_id)
