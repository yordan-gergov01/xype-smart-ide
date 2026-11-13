"""`POST /api/v1/auth/session-key` (ADR-0015, ADR-0016): the client unwraps the key the
server stored and the HMAC keys of its team's requested repositories, the exchange
refuses bad public keys, and limits, the cap and revocations hold. No key byte reaches a
response field other than a `wrapped_key`, a log line or a span."""

import base64
import uuid
from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timedelta

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from support.backend import Harness, HarnessFactory, emitted

from backend.core.auth import client_admin, kek_ring
from backend.services.repositories import RepositoryAdmin
from common_lib.clock import SystemClock
from common_lib.errors import DependencyUnavailableError
from common_lib.secret_bytes import SecretBytes
from common_lib.security import KekPurpose, WrappedKey, frame, hkdf, repository_key_aad, unwrap_key

PATH = "/api/v1/auth/session-key"


def _raw(key: X25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


class Client:
    """The extension's side of the exchange, from raw primitives (as the vectors are)."""

    def __init__(self) -> None:
        self.private = X25519PrivateKey.generate()
        self.public = _raw(self.private)

    def body(self) -> dict[str, str]:
        return {"client_public_key": base64.b64encode(self.public).decode()}

    def body_with(self, repository_ids: list[uuid.UUID]) -> dict[str, object]:
        return {**self.body(), "repository_ids": [str(r) for r in repository_ids]}

    def _wrapping(self, answer: Mapping[str, object], user_id: str) -> bytes:
        server_public = base64.b64decode(str(answer["server_public_key"]))
        shared = self.private.exchange(X25519PublicKey.from_public_bytes(server_public))
        info = frame(b"xype session-key v1", self.public, server_public, user_id)
        return hkdf(SecretBytes(shared), info, salt=str(answer["key_id"]).encode()).reveal()

    def unwrap(self, answer: dict[str, str], user_id: str) -> bytes:
        wrapped = base64.b64decode(answer["wrapped_key"])
        return AESGCM(self._wrapping(answer, user_id)).decrypt(
            wrapped[:12], wrapped[12:], frame("session", answer["key_id"])
        )

    def unwrap_repository_key(
        self, answer: dict[str, object], entry: dict[str, str], user_id: str
    ) -> bytes:
        wrapped = base64.b64decode(entry["wrapped_key"])
        aad = frame("repo-hmac", entry["repository_id"], entry["hmac_key_id"])
        return AESGCM(self._wrapping(answer, user_id)).decrypt(wrapped[:12], wrapped[12:], aad)


async def _login(harness: Harness, http: httpx.AsyncClient) -> tuple[dict[str, str], str]:
    admin = client_admin(harness.settings, harness.auth.deps, SystemClock())
    if await harness.auth.users.find_team("Web") is None:
        await admin.create_team("Web")
    client = await admin.create_client(team="Web", email="d@x.test", display_name="D")
    body = {"client_id": client.client_id, "client_secret": client.client_secret}
    pair = (await http.post("/api/v1/auth/token", json=body)).json()
    return {"Authorization": f"Bearer {pair['access_token']}"}, str(client.user_id)


async def test_the_client_unwraps_the_stored_key(healthy: Harness) -> None:
    client = Client()
    async with healthy.client() as http:
        headers, user_id = await _login(healthy, http)
        response = await http.post(PATH, json=client.body(), headers=headers)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    answer = response.json()
    assert set(answer) == {
        "key_id",
        "server_public_key",
        "wrapped_key",
        "expires_at",
        "repository_keys",
    }
    assert answer["repository_keys"] == []
    session_key = client.unwrap(answer, user_id)
    assert healthy.auth.keys.holds(answer["key_id"]) == session_key
    record, _ = await healthy.auth.keys.get(answer["key_id"])
    assert (record.user_id, record.created_at.tzinfo is not None) == (user_id, True)
    expires = datetime.fromisoformat(answer["expires_at"])
    assert expires - record.created_at == timedelta(hours=12)
    leaked = emitted(healthy)
    for form in (session_key.hex(), base64.b64encode(session_key).decode()):
        assert form not in leaked
    assert answer["wrapped_key"] not in leaked


async def test_each_call_mints_a_new_key_and_server_key(healthy: Harness) -> None:
    async with healthy.client() as http:
        headers, _ = await _login(healthy, http)
        first, second = [
            (await http.post(PATH, json=Client().body(), headers=headers)).json() for _ in range(2)
        ]
    assert first["key_id"] != second["key_id"]
    assert first["server_public_key"] != second["server_public_key"]


@pytest.mark.parametrize(
    "public_key",
    [
        base64.b64encode(bytes(32)).decode(),  # small-order point: all-zero shared secret
        base64.b64encode(bytes(31)).decode(),
        "not base64 at all, but long enough to look like a key=",
        base64.b64encode(bytes(32)).decode().rstrip("="),
    ],
    ids=["all-zero", "short", "garbage", "unpadded"],
)
async def test_refuses_a_bad_public_key_without_echoing_it(
    healthy: Harness, public_key: str
) -> None:
    async with healthy.client() as http:
        headers, _ = await _login(healthy, http)
        response = await http.post(PATH, json={"client_public_key": public_key}, headers=headers)
    assert (response.status_code, response.json()) == (422, {"error": "invalid_request"})
    assert public_key not in response.text


async def test_needs_an_access_token(healthy: Harness) -> None:
    async with healthy.client() as http:
        response = await http.post(PATH, json=Client().body())
    assert (response.status_code, response.json()) == (401, {"error": "invalid_token"})


async def test_eleventh_key_within_a_minute_is_refused(healthy: Harness) -> None:
    async with healthy.client() as http:
        headers, _ = await _login(healthy, http)
        statuses = [
            (await http.post(PATH, json=Client().body(), headers=headers)).status_code
            for _ in range(11)
        ]
    assert statuses == [200] * 10 + [429]


async def test_the_ninth_key_revokes_the_oldest(harness_factory: HarnessFactory) -> None:
    harness = harness_factory([], session_keys_per_minute=20)
    async with harness.client() as http:
        headers, _ = await _login(harness, http)
        ids = [
            (await http.post(PATH, json=Client().body(), headers=headers)).json()["key_id"]
            for _ in range(9)
        ]
    held = [harness.auth.keys.holds(key_id) is not None for key_id in ids]
    assert held == [False] + [True] * 8


async def test_a_revoked_family_mints_nothing(healthy: Harness) -> None:
    async with healthy.client() as http:
        headers, _ = await _login(healthy, http)
        minted = (await http.post(PATH, json=Client().body(), headers=headers)).json()
        record, _ = await healthy.auth.keys.get(minted["key_id"])
        await healthy.auth.keys.revoke_family(record.family_id)
        response = await http.post(PATH, json=Client().body(), headers=headers)
    assert (response.status_code, response.json()) == (401, {"error": "invalid_token"})
    assert healthy.auth.keys.holds(minted["key_id"]) is None


async def _register(harness: Harness, team: str, name: str) -> uuid.UUID:
    if await harness.auth.users.find_team(team) is None:
        await harness.auth.users.create_team(team)
    admin = RepositoryAdmin(
        harness.auth.repositories,
        harness.auth.users,
        kek=kek_ring(harness.settings).current,
        clock=SystemClock(),
    )
    return (await admin.register(team=team, name=name)).repository_id


def _stored_key(harness: Harness, repository_id: uuid.UUID) -> bytes:
    stored = harness.auth.repositories.repositories[repository_id]
    aad = repository_key_aad(str(repository_id), stored.hmac_key_id, stored.wrapped_key.kek_id)
    ring = kek_ring(harness.settings)
    return unwrap_key(ring, KekPurpose.REPO_KEYS, stored.wrapped_key, aad).reveal()


async def test_the_client_unwraps_its_teams_requested_repository_keys(healthy: Harness) -> None:
    client = Client()
    async with healthy.client() as http:
        headers, user_id = await _login(healthy, http)
        first, second = await _register(healthy, "Web", "a"), await _register(healthy, "Web", "b")
        await _register(healthy, "Web", "not-requested")
        other = await _register(healthy, "Other", "a")
        wanted = [first, second, first, other, uuid.uuid4()]
        response = await http.post(PATH, json=client.body_with(wanted), headers=headers)
    assert response.status_code == 200, response.text
    answer = response.json()
    entries = answer["repository_keys"]
    assert sorted(e["repository_id"] for e in entries) == sorted([str(first), str(second)])
    leaked = emitted(healthy)
    for entry in entries:
        repository_id = uuid.UUID(entry["repository_id"])
        stored = healthy.auth.repositories.repositories[repository_id]
        assert entry["hmac_key_id"] == stored.hmac_key_id
        key = client.unwrap_repository_key(answer, entry, user_id)
        assert key == _stored_key(healthy, repository_id)
        for form in (key.hex(), base64.b64encode(key).decode(), entry["wrapped_key"]):
            assert form not in leaked


async def test_another_teams_repository_is_left_out_like_an_unknown_one(
    healthy: Harness,
) -> None:
    async with healthy.client() as http:
        headers, _ = await _login(healthy, http)
        other = await _register(healthy, "Other", "a")
        answers = [
            (await http.post(PATH, json=Client().body_with([wanted]), headers=headers)).json()
            for wanted in (other, uuid.uuid4())
        ]
    assert [a["repository_keys"] for a in answers] == [[], []]


async def test_a_failed_key_read_stores_no_session_key(healthy: Harness) -> None:
    async with healthy.client() as http:
        headers, _ = await _login(healthy, http)
        mine = await _register(healthy, "Web", "a")
        healthy.auth.repositories.unavailable = DependencyUnavailableError("database")
        response = await http.post(PATH, json=Client().body_with([mine]), headers=headers)
    assert response.status_code == 503
    assert healthy.auth.keys.key_ids() == set()


async def test_a_tampered_key_row_fails_the_exchange_and_stores_nothing(healthy: Harness) -> None:
    async with healthy.client() as http:
        headers, _ = await _login(healthy, http)
        mine = await _register(healthy, "Web", "a")
        stored = healthy.auth.repositories.repositories[mine]
        wrapped = stored.wrapped_key
        flipped = bytes([wrapped.ciphertext[0] ^ 1]) + wrapped.ciphertext[1:]
        healthy.auth.repositories.repositories[mine] = replace(
            stored, wrapped_key=WrappedKey(wrapped.kek_id, wrapped.nonce, flipped)
        )
        response = await http.post(PATH, json=Client().body_with([mine]), headers=headers)
    assert response.status_code == 500
    assert healthy.auth.keys.key_ids() == set()
    leaked = emitted(healthy)
    assert "auth.repository_key.unwrap_failed" in leaked
    assert str(mine) in leaked


async def test_more_than_64_repositories_are_refused(healthy: Harness) -> None:
    wanted = [uuid.uuid4() for _ in range(65)]
    async with healthy.client() as http:
        headers, _ = await _login(healthy, http)
        response = await http.post(PATH, json=Client().body_with(wanted), headers=headers)
    assert (response.status_code, response.json()) == (422, {"error": "invalid_request"})
