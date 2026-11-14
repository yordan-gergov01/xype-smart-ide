"""Writes vectors.json from raw primitives only (hashlib, hmac and cryptography's HKDF,
AES-GCM and X25519), independent of `common_lib.security`, so the server test that
reproduces the file checks our composition, and the TypeScript client checks the same.

Run from the repository root with the server venv (Windows path shown):
    server/.venv/Scripts/python -I packages/crypto-vectors/generate.py OUT.json
with OUT.json = packages/crypto-vectors/vectors.json, then format it with Prettier.
Every key is a fixed counting-byte pattern, never a secret (ADR-0015, ADR-0016).
"""

import hashlib
import hmac
import json
import sys
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


def fr(*fields: bytes) -> bytes:
    return b"".join(len(f).to_bytes(4, "big") + f for f in fields)


def hkdf(key: bytes, info: bytes, salt: bytes | None = None) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=info).derive(key)


def raw(private: X25519PrivateKey) -> bytes:
    return private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )


REPO_KEY = bytes(range(32))
SESSION_KEY = bytes(range(100, 132))
vectors: dict[str, object] = {}

vectors["frame"] = [
    {"fields": ["ab", "c"], "hex": fr(b"ab", b"c").hex()},
    {"fields": ["", "x"], "hex": fr(b"", b"x").hex()},
    {"fields": ["é"], "hex": fr("é".encode()).hex()},
]

vectors["hmac_id"] = [
    {
        "key_hex": REPO_KEY.hex(),
        "domain": d,
        "fields": f,
        "id": hmac.new(
            REPO_KEY, fr(d.encode(), *(x.encode() for x in f)), hashlib.sha256
        ).hexdigest(),
    }
    for d, f in [
        ("file", ["src/main.py"]),
        ("dir", ["src"]),
        ("chunk-pos", ["src/main.py", "function", "py:main.run", "0"]),
        ("chunk-content", ["def run():\n    pass\n"]),
    ]
]

vectors["direction_keys"] = {
    "session_key_hex": SESSION_KEY.hex(),
    "c2s_info_hex": fr(b"xype payload", b"c2s").hex(),
    "s2c_info_hex": fr(b"xype payload", b"s2c").hex(),
    "c2s_hex": hkdf(SESSION_KEY, fr(b"xype payload", b"c2s")).hex(),
    "s2c_hex": hkdf(SESSION_KEY, fr(b"xype payload", b"s2c")).hex(),
}

context = {
    "key_id": "key-1",
    "direction": "c2s",
    "message_id": "m-1",
    "request_id": "r-1",
    "field_number": 3,
    "repository_id": "repo-1",
    "obfuscated_file_id": "f" * 64,
    "chunk_hash": "c" * 64,
    "line_start": 10,
    "line_end": 20,
    "leaf_node_id": "e" * 64,
}
aad = fr(
    b"2",
    *(
        str(context[k]).encode()
        for k in [
            "direction",
            "key_id",
            "message_id",
            "request_id",
            "field_number",
            "repository_id",
            "obfuscated_file_id",
            "chunk_hash",
            "line_start",
            "line_end",
            "leaf_node_id",
        ]
    ),
)
nonce = bytes(4) + (7).to_bytes(8, "big")
plaintext = b"def f(): pass\n"
c2s = hkdf(SESSION_KEY, fr(b"xype payload", b"c2s"))
vectors["payload"] = {
    "session_key_hex": SESSION_KEY.hex(),
    "context": context,
    "counter": 7,
    "aad_hex": aad.hex(),
    "nonce_hex": nonce.hex(),
    "plaintext_hex": plaintext.hex(),
    "ciphertext_hex": AESGCM(c2s).encrypt(nonce, plaintext, aad).hex(),
}

client = X25519PrivateKey.from_private_bytes(bytes(range(1, 33)))
server = X25519PrivateKey.from_private_bytes(bytes(range(33, 65)))
shared = client.exchange(server.public_key())
info = fr(b"xype session-key v1", raw(client), raw(server), b"user-1")
wrapping = hkdf(shared, info, salt=b"key-1")
wrap_nonce = bytes(range(12))
vectors["session_key_exchange"] = {
    "client_private_hex": bytes(range(1, 33)).hex(),
    "client_public_hex": raw(client).hex(),
    "server_private_hex": bytes(range(33, 65)).hex(),
    "server_public_hex": raw(server).hex(),
    "key_id": "key-1",
    "user_id": "user-1",
    "info_hex": info.hex(),
    "wrapping_key_hex": wrapping.hex(),
    "session_key_hex": SESSION_KEY.hex(),
    "wrap_nonce_hex": wrap_nonce.hex(),
    "wrap_aad_hex": fr(b"session", b"key-1").hex(),
    "wrapped_key_hex": AESGCM(wrapping)
    .encrypt(wrap_nonce, SESSION_KEY, fr(b"session", b"key-1"))
    .hex(),
}

REPO_ID = b"0192f0e8-7c1a-7b3e-8d2f-4a5b6c7d8e9f"
HMAC_KEY_ID = b"0192f0e8-7c1a-7b3e-8d2f-000000000001"
repo_key = bytes(range(0x90, 0xB0))
repo_nonce = bytes(range(12, 24))
repo_aad = fr(b"repo-hmac", REPO_ID, HMAC_KEY_ID)
vectors["repository_key_wrap"] = {
    "wrapping_key_hex": wrapping.hex(),
    "repository_id": REPO_ID.decode(),
    "hmac_key_id": HMAC_KEY_ID.decode(),
    "repository_key_hex": repo_key.hex(),
    "wrap_nonce_hex": repo_nonce.hex(),
    "wrap_aad_hex": repo_aad.hex(),
    "wrapped_key_hex": AESGCM(wrapping).encrypt(repo_nonce, repo_key, repo_aad).hex(),
}

# Merkle inner hashes (ADR-0018): sorted by node id; node_id and hash as ASCII hex, one
# byte is_leaf, and for a leaf two 4-byte big-endian line numbers.
def inner(children: list[tuple[str, str, bool, tuple[int, int] | None]]) -> str:
    digest = hashlib.sha256()
    for node_id, hash_, is_leaf, lines in sorted(children):
        digest.update(node_id.encode() + hash_.encode() + (b"\x01" if is_leaf else b"\x00"))
        if lines is not None:
            digest.update(lines[0].to_bytes(4, "big") + lines[1].to_bytes(4, "big"))
    return digest.hexdigest()


leaf_children = [
    ("b" * 64, "1" * 64, True, (12, 30)),
    ("a" * 64, "2" * 64, True, (1, 10)),
]
file_hash = inner(leaf_children)
dir_children = [("c" * 64, file_hash, False, None)]
vectors["merkle_inner_hash"] = [
    {"children": [], "hash_hex": inner([])},
    {
        "children": [
            {"node_id": n, "hash": h, "is_leaf": leaf, "lines": list(lines) if lines else None}
            for n, h, leaf, lines in leaf_children
        ],
        "hash_hex": file_hash,
    },
    {
        "children": [
            {"node_id": n, "hash": h, "is_leaf": leaf, "lines": None}
            for n, h, leaf, _ in dir_children
        ],
        "hash_hex": inner(dir_children),
    },
]

Path(sys.argv[1]).write_bytes(
    (json.dumps({"version": 1, **vectors}, indent=2) + "\n").encode("utf-8")
)
