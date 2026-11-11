"""Obfuscated identifiers: keyed HMAC-SHA256 per repository (ADR-0016).

The server never computes identifiers itself (it has no reason to hold a repository
key unwrapped for that); this implementation is the reference the client must match,
and it is used by tests and the seeded sample repo.
"""

import hashlib
import hmac
from enum import StrEnum

from common_lib.secret_bytes import SecretBytes
from common_lib.security.framing import frame


class IdDomain(StrEnum):
    """Keeps identifiers of different kinds from ever colliding."""

    FILE = "file"
    DIR = "dir"
    SYMBOL = "symbol"
    CHUNK_POS = "chunk-pos"
    CHUNK_CONTENT = "chunk-content"


def hmac_id(repo_key: SecretBytes, domain: IdDomain, *fields: str) -> str:
    """64 lowercase hex characters; the repository key is not wiped."""
    digest = hmac.new(repo_key.reveal(), frame(str(domain), *fields), hashlib.sha256)
    return digest.hexdigest()
