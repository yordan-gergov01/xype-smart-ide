"""`RedisRevocationSubscriber`: revocations from `xype:auth:revoked` (ADR-0014).

Redis pub/sub delivers at most once: a message sent while this replica is reconnecting is
lost. The gateway therefore also checks the session key on every stream message, and
revocation deletes the keys before it publishes (ADR-0015); this subscriber only makes
the close immediate.
"""

import asyncio
import json
import uuid
from collections.abc import AsyncGenerator, AsyncIterator

import structlog
from redis.asyncio import Redis
from redis.exceptions import RedisError

from backend.dal.auth.redis_adapters import REVOCATION_CHANNEL
from common_lib.auth import Revocation

logger = structlog.get_logger(__name__)

# How long one poll waits for a message; redis-py applies it as the read timeout of the
# poll, so a quiet channel is never mistaken for a broken connection.
_POLL_SECONDS = 1.0
_MAX_BACKOFF_SECONDS = 10.0
_KINDS = ("user", "family")


class RedisRevocationSubscriber:
    def __init__(self, redis: Redis, *, first_backoff_s: float = 0.5) -> None:
        self._redis = redis
        self._first_backoff = first_backoff_s

    async def revocations(self) -> AsyncGenerator[Revocation]:
        """Every revocation published from now on; reconnects with a capped backoff and
        runs until cancelled."""
        backoff = self._first_backoff
        while True:
            try:
                async for revocation in self._subscribed():
                    backoff = self._first_backoff
                    yield revocation
            except (RedisError, OSError) as error:
                logger.warning("auth.revocations.disconnected", error=type(error).__name__)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, _MAX_BACKOFF_SECONDS)

    async def _subscribed(self) -> AsyncIterator[Revocation]:
        pubsub = self._redis.pubsub(ignore_subscribe_messages=True)
        try:
            await pubsub.subscribe(REVOCATION_CHANNEL)
            logger.info("auth.revocations.subscribed")
            while True:
                message = await pubsub.get_message(timeout=_POLL_SECONDS)
                if message is not None:
                    revocation = _parse(message.get("data"))
                    if revocation is not None:
                        yield revocation
        finally:
            await pubsub.aclose()  # type: ignore[no-untyped-call]  # reason: untyped in redis-py


def _parse(data: object) -> Revocation | None:
    """A revocation, or `None` for anything else on the channel (never raised, never logged
    with its content)."""
    try:
        fields = json.loads(data) if isinstance(data, (str, bytes)) else None
    except (ValueError, RecursionError):  # RecursionError: absurdly nested JSON
        fields = None
    kind = fields.get("kind") if isinstance(fields, dict) else None
    subject = fields.get("id") if isinstance(fields, dict) else None
    if kind not in _KINDS or not isinstance(subject, str):
        logger.warning("auth.revocations.malformed")
        return None
    try:
        return Revocation(kind, uuid.UUID(subject))
    except ValueError:
        logger.warning("auth.revocations.malformed")
        return None
