"""`POST /api/v1/auth/session-key` (ADR-0015): the client unwraps the key the server
stored, the exchange refuses bad public keys, and limits, the cap and revocations hold.
No key byte reaches a response field other than `wrapped_key`, a log line or a span."""

import base64
from datetime import datetime, timedelta

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from support.backend import Harness, HarnessFactory, emitted

from backend.core.auth import client_admin
from common_lib.clock import SystemClock
from common_lib.secret_bytes import SecretBytes
from common_lib.security import frame, hkdf

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

    def unwrap(self, answer: dict[str, str], user_id: str) -> bytes:
        server_public = base64.b64decode(answer["server_public_key"])
        shared = self.private.exchange(X25519PublicKey.from_public_bytes(server_public))
        info = frame(b"xype session-key v1", self.public, server_public, user_id)
        wrapping = hkdf(SecretBytes(shared), info, salt=answer["key_id"].encode())
        wrapped = base64.b64decode(answer["wrapped_key"])
        return AESGCM(wrapping.reveal()).decrypt(
            wrapped[:12], wrapped[12:], frame("session", answer["key_id"])
        )


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
    assert set(answer) == {"key_id", "server_public_key", "wrapped_key", "expires_at"}
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
