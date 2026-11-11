"""Access tokens: only a current EdDSA token of ours is accepted (ADR-0014)."""

import base64
import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import SecretStr
from support.auth import OTHER_SIGNING_KEY, SIGNING_KEY
from support.clock import FakeClock

from backend.services.auth import AccessTokenIssuer, SigningKey
from common_lib.auth import InvalidTokenError, Principal
from common_lib.clock import Clock, SystemClock
from common_lib.errors import InvalidValueError
from common_lib.settings import parse_keyed_secret

PRINCIPAL = Principal(uuid.uuid4(), uuid.uuid4(), "developer")
FAMILY = uuid.uuid4()
CURRENT = SigningKey.from_seed(*parse_keyed_secret(SecretStr(SIGNING_KEY)))
PREVIOUS = SigningKey.from_seed(*parse_keyed_secret(SecretStr(OTHER_SIGNING_KEY)))


def issuer(
    *, current: SigningKey = CURRENT, previous: SigningKey | None = None, clock: Clock | None = None
) -> AccessTokenIssuer:
    return AccessTokenIssuer(
        current, previous, issuer="xype", ttl_seconds=900, clock=clock or SystemClock()
    )


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def forged(header: dict[str, object], claims: dict[str, object], signature: bytes = b"") -> str:
    parts = [_b64(json.dumps(header).encode()), _b64(json.dumps(claims).encode())]
    return ".".join([*parts, _b64(signature)])


def valid_claims() -> dict[str, object]:
    token = issuer().issue(PRINCIPAL, FAMILY)
    return jwt.decode(token, options={"verify_signature": False})


def test_round_trips_the_principal_and_family() -> None:
    claims = issuer().verify(issuer().issue(PRINCIPAL, FAMILY))
    assert (claims.principal, claims.family_id) == (PRINCIPAL, FAMILY)
    assert claims.expires_at - datetime.now(UTC) <= timedelta(seconds=900)


def test_previous_key_still_verifies_during_a_rotation() -> None:
    old = issuer(current=PREVIOUS).issue(PRINCIPAL, FAMILY)
    assert issuer(previous=PREVIOUS).verify(old).principal == PRINCIPAL
    with pytest.raises(InvalidTokenError):
        issuer().verify(old)


def test_expiry_and_issue_time_follow_the_clock_with_30_s_skew() -> None:
    clock = FakeClock(datetime(2026, 11, 11, 9, 0, tzinfo=UTC))
    tokens = issuer(clock=clock)
    token = tokens.issue(PRINCIPAL, FAMILY)
    clock.advance(timedelta(seconds=900 + 29))
    assert tokens.verify(token).principal == PRINCIPAL
    clock.advance(timedelta(seconds=1))
    with pytest.raises(InvalidTokenError):
        tokens.verify(token)
    future = issuer(clock=FakeClock(clock.now() + timedelta(minutes=5))).issue(PRINCIPAL, FAMILY)
    with pytest.raises(InvalidTokenError):
        tokens.verify(future)


def test_refuses_another_audience_issuer_or_missing_claim() -> None:
    key = CURRENT.signer
    for change in ({"aud": "someone-else"}, {"iss": "evil"}, {"fam": None}):
        claims = {k: v for k, v in {**valid_claims(), **change}.items() if v is not None}
        token = jwt.encode(claims, key, algorithm="EdDSA", headers={"kid": CURRENT.key_id})
        with pytest.raises(InvalidTokenError):
            issuer().verify(token)


def test_refuses_alg_none_and_an_unknown_kid() -> None:
    with pytest.raises(InvalidTokenError):
        issuer().verify(forged({"alg": "none", "kid": CURRENT.key_id}, valid_claims()))
    stranger = Ed25519PrivateKey.generate()
    token = jwt.encode(valid_claims(), stranger, algorithm="EdDSA", headers={"kid": "other"})
    with pytest.raises(InvalidTokenError):
        issuer().verify(token)


def test_ignores_a_jku_header_pointing_to_an_attacker_key() -> None:
    stranger = Ed25519PrivateKey.generate()
    headers = {"kid": CURRENT.key_id, "jku": "https://attacker.example/keys.json"}
    token = jwt.encode(valid_claims(), stranger, algorithm="EdDSA", headers=headers)
    with pytest.raises(InvalidTokenError):
        issuer().verify(token)


def test_refuses_hs256_signed_with_the_public_key() -> None:
    """Algorithm confusion: an HMAC "signed" with our public key must not verify."""
    public = CURRENT.signer.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    header = {"alg": "HS256", "kid": CURRENT.key_id, "typ": "JWT"}
    signing_input = (
        f"{_b64(json.dumps(header).encode())}.{_b64(json.dumps(valid_claims()).encode())}"
    )
    signature = hmac.new(public, signing_input.encode(), hashlib.sha256).digest()
    with pytest.raises(InvalidTokenError):
        issuer().verify(f"{signing_input}.{_b64(signature)}")


@pytest.mark.parametrize("token", ["", "not-a-jwt", "a.b.c", "x" * 5000])
def test_refuses_garbage(token: str) -> None:
    with pytest.raises(InvalidTokenError):
        issuer().verify(token)


def test_refuses_a_tampered_role() -> None:
    token = issuer().issue(PRINCIPAL, FAMILY)
    header, _, signature = token.split(".")
    claims = {**valid_claims(), "role": "admin"}
    with pytest.raises(InvalidTokenError):
        issuer().verify(f"{header}.{_b64(json.dumps(claims).encode())}.{signature}")


def test_refuses_a_previous_key_with_the_current_id() -> None:
    with pytest.raises(InvalidValueError):
        issuer(previous=SigningKey(CURRENT.key_id, PREVIOUS.signer))
