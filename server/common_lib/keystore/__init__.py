"""The Redis adapter of `KeyStore`, for the non-evicting `keystore` Redis (ADR-0015).

Shared by the backend (all operations) and the indexer (`get` only, through an ACL user
that may only `GET xype:sk:*`). It talks to Redis and to nothing else.
"""

from common_lib.keystore.redis_store import RedisKeyStore

__all__ = ["RedisKeyStore"]
