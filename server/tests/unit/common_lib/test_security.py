import dataclasses

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from common_lib.errors import InvalidValueError
from common_lib.secret_bytes import SecretBytes
from common_lib.security import (
    MAX_ENCRYPTIONS_PER_DIRECTION,
    CryptoBox,
    DecryptionError,
    Direction,
    EncryptedPayload,
    IdDomain,
    Kek,
    KekPurpose,
    KekRing,
    KeyExchangeError,
    NonceError,
    PayloadContext,
    counter_of,
    direction_key,
    frame,
    hkdf,
    hmac_id,
    new_key,
    nonce_for,
    server_exchange,
    unwrap_key,
    wrap_key,
)

KEY = SecretBytes(bytes(range(32)))
CONTEXT = PayloadContext(
    key_id="key-1",
    direction=Direction.CLIENT_TO_SERVER,
    message_id="m-1",
    request_id="r-1",
    field_number=3,
    repository_id="repo-1",
    obfuscated_file_id="f" * 64,
    chunk_hash="c" * 64,
    line_start=10,
    line_end=20,
    leaf_node_id="e" * 64,
)


def raw_public(private: X25519PrivateKey) -> bytes:
    return private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )


class TestFrame:
    def test_field_boundaries_are_unambiguous(self) -> None:
        assert frame("ab", "c") != frame("a", "bc")
        assert frame("") != frame()

    def test_encodes_lengths_big_endian(self) -> None:
        assert frame(b"xy", 7) == b"\x00\x00\x00\x02xy\x00\x00\x00\x017"

    def test_refuses_booleans(self) -> None:
        with pytest.raises(InvalidValueError):
            frame(True)


class TestHmacId:
    def test_is_keyed_and_domain_separated(self) -> None:
        other = SecretBytes(bytes(32))
        file_id = hmac_id(KEY, IdDomain.FILE, "src/main.py")
        assert len(file_id) == 64
        assert file_id == hmac_id(KEY, IdDomain.FILE, "src/main.py")
        assert file_id != hmac_id(other, IdDomain.FILE, "src/main.py")
        assert file_id != hmac_id(KEY, IdDomain.DIR, "src/main.py")

    def test_fields_do_not_run_together(self) -> None:
        assert hmac_id(KEY, IdDomain.CHUNK_POS, "a", "bc") != hmac_id(
            KEY, IdDomain.CHUNK_POS, "ab", "c"
        )


class TestKeyWrapping:
    RING = KekRing(Kek("kek-1", SecretBytes(bytes(range(1, 33)))))

    def test_round_trips_and_binds_the_aad(self) -> None:
        wrapped = wrap_key(self.RING.current, KekPurpose.SESSION_KEYS, KEY, b"aad")
        assert KEY.reveal() not in wrapped.ciphertext
        assert (
            unwrap_key(self.RING, KekPurpose.SESSION_KEYS, wrapped, b"aad").reveal() == KEY.reveal()
        )
        with pytest.raises(DecryptionError):
            unwrap_key(self.RING, KekPurpose.SESSION_KEYS, wrapped, b"other")

    def test_subkeys_separate_the_purposes(self) -> None:
        wrapped = wrap_key(self.RING.current, KekPurpose.SESSION_KEYS, KEY, b"aad")
        with pytest.raises(DecryptionError):
            unwrap_key(self.RING, KekPurpose.REPO_KEYS, wrapped, b"aad")

    def test_refuses_an_unknown_kek(self) -> None:
        wrapped = wrap_key(Kek("kek-9", SecretBytes(bytes(32))), KekPurpose.SESSION_KEYS, KEY, b"")
        with pytest.raises(DecryptionError):
            unwrap_key(self.RING, KekPurpose.SESSION_KEYS, wrapped, b"")

    def test_refuses_a_short_kek(self) -> None:
        with pytest.raises(InvalidValueError):
            Kek("kek-1", SecretBytes(bytes(16)))


class TestExchange:
    def test_both_sides_derive_the_same_wrapping_key(self) -> None:
        client, server = X25519PrivateKey.generate(), X25519PrivateKey.generate()
        result = server_exchange(raw_public(client), key_id="k", user_id="u", server_private=server)
        assert result.server_public_key == raw_public(server)
        shared = SecretBytes(client.exchange(server.public_key()))
        info = frame(b"xype session-key v1", raw_public(client), raw_public(server), "u")
        assert hkdf(shared, info, salt=b"k").reveal() == result.wrapping_key.reveal()

    def test_binds_the_user(self) -> None:
        client, server = X25519PrivateKey.generate(), X25519PrivateKey.generate()
        a = server_exchange(raw_public(client), key_id="k", user_id="u1", server_private=server)
        b = server_exchange(raw_public(client), key_id="k", user_id="u2", server_private=server)
        assert a.wrapping_key.reveal() != b.wrapping_key.reveal()

    @pytest.mark.parametrize("public", [bytes(32), b"short"])
    def test_refuses_small_order_and_malformed_keys(self, public: bytes) -> None:
        with pytest.raises(KeyExchangeError):
            server_exchange(public, key_id="k", user_id="u")


class TestNonces:
    def test_counter_round_trips(self) -> None:
        assert counter_of(nonce_for(7)) == 7
        assert nonce_for(1)[:4] == bytes(4)

    @pytest.mark.parametrize("counter", [0, -1, MAX_ENCRYPTIONS_PER_DIRECTION + 1])
    def test_refuses_counters_outside_the_budget(self, counter: int) -> None:
        with pytest.raises(NonceError):
            nonce_for(counter)

    @pytest.mark.parametrize("nonce", [bytes(11), b"\x01" + bytes(11), b"\x00" * 4 + b"\xff" * 8])
    def test_refuses_other_nonce_shapes(self, nonce: bytes) -> None:
        with pytest.raises(NonceError):
            counter_of(nonce)


class TestCryptoBox:
    # The client seals client-to-server payloads; the server (the default) opens them.
    client = CryptoBox(sends=Direction.CLIENT_TO_SERVER)
    box = CryptoBox(sends=Direction.SERVER_TO_CLIENT)

    def sealed(self, context: PayloadContext = CONTEXT) -> EncryptedPayload:
        return self.client.seal(KEY, 1, SecretBytes(b"def f(): pass\n"), context)

    def test_each_side_encrypts_in_its_own_direction_only(self) -> None:
        with pytest.raises(NonceError):
            self.box.seal(KEY, 1, SecretBytes(b"x"), CONTEXT)
        with pytest.raises(DecryptionError):
            self.client.open(KEY, self.sealed(), CONTEXT)
        reply = dataclasses.replace(CONTEXT, direction=Direction.SERVER_TO_CLIENT)
        sealed = self.box.seal(KEY, 1, SecretBytes(b"y"), reply)
        assert self.client.open(KEY, sealed, reply).reveal() == b"y"

    def test_round_trips(self) -> None:
        payload = self.sealed()
        assert b"def f" not in payload.ciphertext
        assert self.box.open(KEY, payload, CONTEXT).reveal() == b"def f(): pass\n"

    def test_matches_aes_gcm_under_the_direction_key(self) -> None:
        payload = self.sealed()
        key = direction_key(KEY, Direction.CLIENT_TO_SERVER).reveal()
        assert (
            AESGCM(key).decrypt(payload.nonce, payload.ciphertext, CONTEXT.aad())
            == b"def f(): pass\n"
        )

    @pytest.mark.parametrize(
        "change",
        [
            {"chunk_hash": "d" * 64},
            {"leaf_node_id": "d" * 64},
            {"repository_id": "repo-2"},
            {"line_end": 21},
            {"message_id": "m-2"},
            {"direction": Direction.SERVER_TO_CLIENT},
        ],
    )
    def test_refuses_a_moved_or_reflected_payload(self, change: dict[str, object]) -> None:
        with pytest.raises(DecryptionError):
            self.box.open(KEY, self.sealed(), dataclasses.replace(CONTEXT, **change))  # type: ignore[arg-type]

    def test_refuses_a_foreign_key_id_and_a_wrong_key(self) -> None:
        with pytest.raises(DecryptionError):
            self.box.open(KEY, self.sealed(), dataclasses.replace(CONTEXT, key_id="key-2"))
        with pytest.raises(DecryptionError):
            self.box.open(new_key(), self.sealed(), CONTEXT)

    def test_refuses_a_tampered_ciphertext(self) -> None:
        payload = self.sealed()
        flipped = bytes([payload.ciphertext[0] ^ 1]) + payload.ciphertext[1:]
        with pytest.raises(DecryptionError):
            self.box.open(KEY, dataclasses.replace(payload, ciphertext=flipped), CONTEXT)


def test_a_chunk_context_needs_its_leaf_id() -> None:
    """Without the leaf id a chunk payload would silently lose its leaf binding."""
    for change in ({"leaf_node_id": ""}, {"chunk_hash": ""}):
        with pytest.raises(InvalidValueError):
            dataclasses.replace(CONTEXT, **change)  # type: ignore[arg-type]
