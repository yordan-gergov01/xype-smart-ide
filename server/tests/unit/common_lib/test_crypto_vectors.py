"""The server reproduces `packages/crypto-vectors/vectors.json`. The vectors were made
from raw primitives, not from `common_lib.security`, and the TypeScript client checks
the same file (slice 9), so both sides agree on framing, ids, keys and the AAD."""

import json
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from common_lib.secret_bytes import SecretBytes
from common_lib.security import (
    CryptoBox,
    Direction,
    IdDomain,
    PayloadContext,
    direction_key,
    frame,
    hmac_id,
    seal_session_key,
    server_exchange,
)

# reason: Any - the vectors file is plain JSON of several shapes.
VECTORS: dict[str, Any] = json.loads(
    (
        Path(__file__).resolve().parents[4] / "packages" / "crypto-vectors" / "vectors.json"
    ).read_text("utf-8")
)


def test_frame() -> None:
    for case in VECTORS["frame"]:
        assert frame(*case["fields"]).hex() == case["hex"]


def test_hmac_id() -> None:
    for case in VECTORS["hmac_id"]:
        key = SecretBytes(bytes.fromhex(case["key_hex"]))
        assert hmac_id(key, IdDomain(case["domain"]), *case["fields"]) == case["id"]


def test_direction_keys() -> None:
    case = VECTORS["direction_keys"]
    assert frame("xype payload", "c2s").hex() == case["c2s_info_hex"]
    assert frame("xype payload", "s2c").hex() == case["s2c_info_hex"]
    session = SecretBytes(bytes.fromhex(case["session_key_hex"]))
    assert direction_key(session, Direction.CLIENT_TO_SERVER).reveal().hex() == case["c2s_hex"]
    assert direction_key(session, Direction.SERVER_TO_CLIENT).reveal().hex() == case["s2c_hex"]


def test_payload() -> None:
    case = VECTORS["payload"]
    fields = dict(case["context"])
    context = PayloadContext(**{**fields, "direction": Direction(fields["direction"])})
    assert context.aad().hex() == case["aad_hex"]
    session = SecretBytes(bytes.fromhex(case["session_key_hex"]))
    # The vector is a client-to-server payload: sealed as the client, opened as the server.
    sealed = CryptoBox(sends=Direction.CLIENT_TO_SERVER).seal(
        session, case["counter"], SecretBytes(bytes.fromhex(case["plaintext_hex"])), context
    )
    assert sealed.nonce.hex() == case["nonce_hex"]
    assert sealed.ciphertext.hex() == case["ciphertext_hex"]
    server = CryptoBox(sends=Direction.SERVER_TO_CLIENT)
    assert server.open(session, sealed, context).reveal().hex() == case["plaintext_hex"]


def test_session_key_exchange() -> None:
    case = VECTORS["session_key_exchange"]
    server = X25519PrivateKey.from_private_bytes(bytes.fromhex(case["server_private_hex"]))
    result = server_exchange(
        bytes.fromhex(case["client_public_hex"]),
        key_id=case["key_id"],
        user_id=case["user_id"],
        server_private=server,
    )
    assert result.server_public_key.hex() == case["server_public_hex"]
    assert result.wrapping_key.reveal().hex() == case["wrapping_key_hex"]
    aad = frame("session", case["key_id"])
    assert aad.hex() == case["wrap_aad_hex"]
    unwrapped = AESGCM(result.wrapping_key.reveal()).decrypt(
        bytes.fromhex(case["wrap_nonce_hex"]), bytes.fromhex(case["wrapped_key_hex"]), aad
    )
    assert unwrapped.hex() == case["session_key_hex"]
    sealed = seal_session_key(
        result.wrapping_key,
        case["key_id"],
        SecretBytes(unwrapped),
        nonce=bytes.fromhex(case["wrap_nonce_hex"]),
    )
    assert sealed.hex() == case["wrap_nonce_hex"] + case["wrapped_key_hex"]
