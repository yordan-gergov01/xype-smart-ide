"""Builds repository registration from the settings, for the admin CLI (and the admin
dashboard in Phase 7)."""

from backend.core.auth import kek_ring
from backend.core.connections import Database
from backend.core.settings import BackendSettings
from backend.dal.repositories import SqlRepositoryStore
from backend.services.repositories import RepositoryAdmin, TeamDirectory
from common_lib.clock import Clock


def repository_admin(
    settings: BackendSettings, database: Database, teams: TeamDirectory, clock: Clock
) -> RepositoryAdmin:
    """New keys are wrapped under the current KEK."""
    store = SqlRepositoryStore(database.write_sessions)
    return RepositoryAdmin(store, teams, kek=kek_ring(settings).current, clock=clock)
