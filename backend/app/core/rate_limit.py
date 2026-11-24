"""Fixed-window rate limiting backed by Redis.

Each limit is a Redis counter keyed by (name, subject, window). INCR + EXPIRE is
atomic enough for this purpose and works the same across many API replicas,
which an in-process counter would not.

Availability over strictness: if Redis is unreachable the request is allowed and
a warning is logged. Logins must keep working when the cache is down; password
hashing (Argon2id) still makes online guessing slow in that situation.
"""

import hashlib
import logging
import time
from dataclasses import dataclass

from redis.asyncio import Redis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Limit:
    name: str
    max_hits: int
    window_seconds: int


@dataclass(frozen=True)
class Decision:
    allowed: bool
    retry_after: int  # seconds until the current window ends (0 when allowed)


class RateLimiter:
    def __init__(self, redis: Redis | None, enabled: bool = True) -> None:
        self._redis = redis
        self.enabled = enabled and redis is not None

    @staticmethod
    def _key(limit: Limit, subject: str) -> tuple[str, int]:
        now = int(time.time())
        window = now // limit.window_seconds
        # Hash the subject so emails/IPs are not stored in Redis in plain text.
        digest = hashlib.sha256(subject.lower().encode()).hexdigest()[:32]
        ttl = limit.window_seconds - (now % limit.window_seconds)
        return f"nexus:rl:{limit.name}:{digest}:{window}", max(1, ttl)

    async def hit(self, limit: Limit, subject: str) -> Decision:
        """Count one attempt and say whether it is within the limit."""
        if not self.enabled:
            return Decision(True, 0)
        key, ttl = self._key(limit, subject)
        try:
            async with self._redis.pipeline(transaction=True) as pipe:  # type: ignore[union-attr]
                pipe.incr(key)
                pipe.expire(key, ttl + 1)
                count, _ = await pipe.execute()
        except (RedisError, OSError) as exc:
            logger.warning("rate limiter unavailable, allowing request", extra={"error": str(exc)})
            return Decision(True, 0)
        return Decision(count <= limit.max_hits, 0 if count <= limit.max_hits else ttl)

    async def peek(self, limit: Limit, subject: str) -> Decision:
        """Check a counter without incrementing it (e.g. failed logins so far)."""
        if not self.enabled:
            return Decision(True, 0)
        key, ttl = self._key(limit, subject)
        try:
            raw = await self._redis.get(key)  # type: ignore[union-attr]
        except (RedisError, OSError) as exc:
            logger.warning("rate limiter unavailable, allowing request", extra={"error": str(exc)})
            return Decision(True, 0)
        count = int(raw or 0)
        return Decision(count < limit.max_hits, 0 if count < limit.max_hits else ttl)

    async def ping(self) -> None:
        if self._redis is None:
            raise RuntimeError("Redis is not configured")
        await self._redis.ping()

    async def aclose(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()


def build_rate_limiter(redis_url: str, enabled: bool) -> RateLimiter:
    if not enabled:
        return RateLimiter(None, enabled=False)
    client = Redis.from_url(redis_url, socket_timeout=0.5, socket_connect_timeout=0.5)
    return RateLimiter(client, enabled=True)
