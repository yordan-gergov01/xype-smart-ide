"""`RedisKeyStore`: session keys wrapped under the KEK in the `keystore` Redis.

Layout (all keys expire with the session key, ADR-0015):
- `xype:sk:<key_id>`: one JSON string with the record and the wrapped key, so the
  indexer's ACL needs only `GET`;
- `xype:skb:<key_id>`: the stream the key is bound to;
- `xype:skn:<key_id>:c2s`: the highest counter received from the client;
- `xype:skc:<key_id>:s2c`: the last counter this server allocated for sending;
- `xype:sku:<user_id>`, `xype:skf:<family_id>`: the key ids of a user and of a family.
The server only receives client-to-server payloads and only sends server-to-client
ones, so the counters exist for those directions only (no nonce is ever used twice
under one direction key). Every write is one Lua script, so revocation cannot race a
concurrent `put`. Redis errors become `DependencyUnavailableError("keystore")`.
"""

import base64
import binascii
import json
from collections.abc import Awaitable, Callable
from datetime import datetime

from redis.asyncio import Redis
from redis.exceptions import RedisError

from common_lib.errors import DependencyUnavailableError, InvalidValueError
from common_lib.secret_bytes import SecretBytes
from common_lib.security import (
    DecryptionError,
    Direction,
    DuplicateKeyError,
    KekPurpose,
    KekRing,
    KeyBoundElsewhereError,
    KeyNotFoundError,
    NonceError,
    SessionKeyRecord,
    WrappedKey,
    frame,
    unwrap_key,
    wrap_key,
)

_DEPENDENCY = "keystore"
_RECEIVED, _SENT = Direction.CLIENT_TO_SERVER, Direction.SERVER_TO_CLIENT
# A new key: never overwrites one (its counters would restart), and index sets only
# ever extend their TTL (EXPIRE GT), so they outlive every key they list.
_PUT = """
if not redis.call('SET', KEYS[1], ARGV[1], 'NX', 'EX', ARGV[3]) then return 0 end
for i = 2, 3 do
  redis.call('SADD', KEYS[i], ARGV[2])
  if redis.call('EXPIRE', KEYS[i], ARGV[3], 'GT') == 0 then
    redis.call('EXPIRE', KEYS[i], ARGV[3], 'NX')
  end
end
return 1
"""
# Each of these first checks that the session key still exists and takes its TTL.
_BIND = """
local ttl = redis.call('PTTL', KEYS[1])
if ttl <= 0 then return -1 end
if redis.call('SET', KEYS[2], ARGV[1], 'NX', 'PX', ttl) then return 1 end
if redis.call('GET', KEYS[2]) == ARGV[1] then return 1 end
return 0
"""
_ADVANCE = """
local ttl = redis.call('PTTL', KEYS[1])
if ttl <= 0 then return -1 end
local seen = tonumber(redis.call('GET', KEYS[2]) or '0')
if tonumber(ARGV[1]) <= seen then return 0 end
redis.call('SET', KEYS[2], ARGV[1], 'PX', ttl)
return 1
"""
_NEXT = """
local ttl = redis.call('PTTL', KEYS[1])
if ttl <= 0 then return -1 end
local counter = redis.call('INCR', KEYS[2])
redis.call('PEXPIRE', KEYS[2], ttl)
return counter
"""
# Atomic: deletes every key the index lists, with its binding and counters, removes
# the ids from the other index too, then the index itself.
_REVOKE = """
for _, id in ipairs(redis.call('SMEMBERS', KEYS[1])) do
  local raw = redis.call('GET', 'xype:sk:' .. id)
  -- A malformed record must not stop the revocation of the keys after it.
  local ok, record = pcall(cjson.decode, raw or '')
  if ok and type(record) == 'table' and type(record.user_id) == 'string'
     and type(record.family_id) == 'string' then
    redis.call('SREM', 'xype:sku:' .. record.user_id, id)
    redis.call('SREM', 'xype:skf:' .. record.family_id, id)
  end
  redis.call('DEL', 'xype:sk:' .. id, 'xype:skb:' .. id,
             'xype:skn:' .. id .. ':c2s', 'xype:skc:' .. id .. ':s2c')
end
redis.call('DEL', KEYS[1])
return 1
"""


def _key(key_id: str) -> str:
    return f"xype:sk:{key_id}"


def _aad(record: SessionKeyRecord, kek_id: str) -> bytes:
    """Binds every field of the record, so none can be changed or swapped unnoticed."""
    return frame(
        "session-kek",
        record.key_id,
        record.user_id,
        record.family_id,
        record.created_at.isoformat(),
        kek_id,
    )


class RedisKeyStore:
    def __init__(self, client: Redis, kek: KekRing) -> None:
        self._redis = client
        self._kek = kek
        self._put = client.register_script(_PUT)
        self._bind = client.register_script(_BIND)
        self._advance = client.register_script(_ADVANCE)
        self._next = client.register_script(_NEXT)
        self._revoke = client.register_script(_REVOKE)

    async def put(self, record: SessionKeyRecord, key: SecretBytes, *, ttl_seconds: int) -> None:
        if ttl_seconds <= 0:
            raise InvalidValueError("a session key needs a positive TTL")
        kek = self._kek.current
        wrapped = wrap_key(kek, KekPurpose.SESSION_KEYS, key, _aad(record, kek.kek_id))
        keys = [_key(record.key_id), f"xype:sku:{record.user_id}", f"xype:skf:{record.family_id}"]
        args: list[str | int] = [json.dumps(_encode(record, wrapped)), record.key_id, ttl_seconds]
        if await self._call(lambda: self._put(keys=keys, args=args)) == 0:
            raise DuplicateKeyError("a session key with this id already exists")

    async def get(self, key_id: str) -> tuple[SessionKeyRecord, SecretBytes]:
        raw = await self._call(lambda: self._redis.get(_key(key_id)))
        if raw is None:
            raise KeyNotFoundError("the session key is unknown or expired")
        record, wrapped = _decode(raw)
        if record.key_id != key_id:
            raise DecryptionError("the stored key record belongs to another key")
        aad = _aad(record, wrapped.kek_id)
        return record, unwrap_key(self._kek, KekPurpose.SESSION_KEYS, wrapped, aad)

    async def bind_stream(self, key_id: str, session_id: str) -> None:
        keys = [_key(key_id), f"xype:skb:{key_id}"]
        self._check(await self._call(lambda: self._bind(keys=keys, args=[session_id])))

    async def advance_nonce(self, key_id: str, direction: Direction, counter: int) -> None:
        if direction is not _RECEIVED:
            raise NonceError("the server only receives client-to-server payloads")
        keys = [_key(key_id), f"xype:skn:{key_id}:{direction}"]
        self._check(await self._call(lambda: self._advance(keys=keys, args=[counter])), nonce=True)

    async def next_counter(self, key_id: str, direction: Direction) -> int:
        if direction is not _SENT:
            raise NonceError("the server only sends server-to-client payloads")
        keys = [_key(key_id), f"xype:skc:{key_id}:{direction}"]
        result = int(await self._call(lambda: self._next(keys=keys, args=[])))
        self._check(result)
        return result

    async def revoke_user(self, user_id: str) -> None:
        await self._call(lambda: self._revoke(keys=[f"xype:sku:{user_id}"], args=[]))

    async def revoke_family(self, family_id: str) -> None:
        await self._call(lambda: self._revoke(keys=[f"xype:skf:{family_id}"], args=[]))

    @staticmethod
    def _check(result: object, *, nonce: bool = False) -> None:
        if result == -1:
            raise KeyNotFoundError("the session key is unknown or expired")
        if result == 0 and nonce:
            raise NonceError("the nonce counter was not above the highest seen")
        if result == 0:
            raise KeyBoundElsewhereError("the session key belongs to another stream")

    async def _call[T](self, operation: Callable[[], Awaitable[T]]) -> T:
        try:
            return await operation()
        except (RedisError, OSError):
            raise DependencyUnavailableError(_DEPENDENCY) from None


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _encode(record: SessionKeyRecord, wrapped: WrappedKey) -> dict[str, str]:
    return {
        "key_id": record.key_id,
        "user_id": record.user_id,
        "family_id": record.family_id,
        "created_at": record.created_at.isoformat(),
        "kek_id": wrapped.kek_id,
        "nonce": _b64(wrapped.nonce),
        "wrapped": _b64(wrapped.ciphertext),
    }


def _decode(raw: bytes | str) -> tuple[SessionKeyRecord, WrappedKey]:
    try:
        data = json.loads(raw)
        record = SessionKeyRecord(
            data["key_id"],
            data["user_id"],
            data["family_id"],
            datetime.fromisoformat(data["created_at"]),
        )
        wrapped = WrappedKey(
            data["kek_id"],
            base64.b64decode(data["nonce"], validate=True),
            base64.b64decode(data["wrapped"], validate=True),
        )
    except (ValueError, KeyError, TypeError, binascii.Error):
        raise DecryptionError("the stored key record is malformed") from None
    return record, wrapped
