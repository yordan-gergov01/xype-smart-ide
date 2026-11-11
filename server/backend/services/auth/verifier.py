"""The first `CredentialVerifier`: client credentials an admin created (ADR-0014)."""

from backend.services.auth.peppers import PepperRing
from backend.services.auth.ports import UserStore
from common_lib.auth import Credential, InvalidCredentialsError, VerifiedCredential


class ClientCredentialVerifier:
    """Checks `client_id` and `client_secret` against the peppered hash in `users`."""

    def __init__(self, users: UserStore, peppers: PepperRing) -> None:
        self._users = users
        self._peppers = peppers

    async def verify(self, credential: Credential) -> VerifiedCredential:
        stored = await self._users.find_credentials(credential.client_id)
        # Hashed and compared even for an unknown id, so timing reveals nothing.
        matches = self._peppers.secret_matches(
            credential.secret.reveal(), stored.secret_hash if stored else None
        )
        if stored is None or not matches or not stored.is_active:
            raise InvalidCredentialsError
        return VerifiedCredential(stored.principal, stored.secret_hash)
