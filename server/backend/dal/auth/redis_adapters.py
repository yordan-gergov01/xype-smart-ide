"""Redis adapters of the auth feature: the GCRA rate limiter and the revocation
publisher (ADR-0014). Keys and messages carry ids only, never a token or secret."""

import json
import math
from datetime import datetime

from redis.asyncio import Redis
from redis.exceptions import RedisError

from common_lib.auth import RateDecision, Revocation
from common_lib.errors import DependencyUnavailableError

KEY_PREFIX = "xype:rl:"
REVOCATION_CHANNEL = "xype:auth:revoked"

# GCRA in one atomic round trip. KEYS[1]: the bucket. ARGV: now (ms), the emission
# interval (period / limit, ms), the burst tolerance (period, ms) and the cost (0 checks
# only). The stored value is the theoretical arrival time (TAT); it expires when idle.
_GCRA = """
local now = tonumber(ARGV[1])
local interval = tonumber(ARGV[2])
local burst = tonumber(ARGV[3])
local cost = tonumber(ARGV[4])
local tat = tonumber(redis.call('GET', KEYS[1]) or now)
if tat < now then tat = now end
local new_tat = tat + interval * math.max(cost, 1)
local allow_at = new_tat - burst
if allow_at > now then
  return {0, allow_at - now}
end
if cost > 0 then
  redis.call('SET', KEYS[1], new_tat, 'PX', math.ceil(new_tat - now))
end
return {1, 0}
"""


def gcra_interval_ms(limit: int, period_seconds: int) -> float:
    return period_seconds * 1000 / limit


class RedisRateLimiter:
    """Shared by every replica; "now" comes from the caller's clock (tests control it).
    Replica clocks are assumed to be NTP-synchronised to well under a second."""

    def __init__(self, redis: Redis) -> None:
        self._script = redis.register_script(_GCRA)

    async def acquire(
        self, key: str, *, limit: int, period_seconds: int, now: datetime, cost: int = 1
    ) -> RateDecision:
        now_ms = math.floor(now.timestamp() * 1000)
        args = [now_ms, gcra_interval_ms(limit, period_seconds), period_seconds * 1000, cost]
        try:
            allowed, retry_ms = await self._script(keys=[KEY_PREFIX + key], args=args)
        except (RedisError, OSError):
            raise DependencyUnavailableError("redis") from None
        return RateDecision(bool(allowed), float(retry_ms) / 1000)


class RedisRevocationPublisher:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def publish(self, revocation: Revocation) -> None:
        message = json.dumps({"kind": revocation.kind, "id": str(revocation.subject_id)})
        try:
            await self._redis.publish(REVOCATION_CHANNEL, message)
        except (RedisError, OSError):
            raise DependencyUnavailableError("redis") from None
