"""Registered repositories as the backend's services and data access share them: what
registration writes and the wrapped HMAC keys a session-key exchange reads (ADR-0016)."""

import uuid
from dataclasses import dataclass, field

from common_lib.errors import XypeError
from common_lib.security import WrappedKey


@dataclass(frozen=True, slots=True)
class NewRepository:
    """A repository and its first HMAC key, written in one transaction. The ids are
    made before the insert because the key's AAD binds them."""

    repository_id: uuid.UUID
    team_id: uuid.UUID
    name: str
    remote_url: str | None
    is_template: bool
    hmac_key_id: str
    wrapped_key: WrappedKey = field(repr=False)


@dataclass(frozen=True, slots=True)
class StoredRepositoryKey:
    """The active HMAC key of a repository, still wrapped under the KEK."""

    repository_id: uuid.UUID
    hmac_key_id: str
    wrapped_key: WrappedKey = field(repr=False)


class DuplicateRepositoryError(XypeError):
    """The team already has a repository of this name."""
