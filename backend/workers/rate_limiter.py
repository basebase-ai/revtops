"""
Redis-backed token bucket rate limiter shared across Celery workers.

Provides a distributed rate limiter that multiple worker processes can
share to respect external API rate limits (e.g., Perplexity, Apollo).

Usage:
    limiter = RedisRateLimiter(redis_url, key="perplexity", rate_per_minute=200)
    await limiter.acquire()  # Blocks until a token is available
    # ... make API call ...
"""

import asyncio
import logging
import time
from typing import Optional

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

# Minimum sleep between acquire retries (seconds)
_MIN_RETRY_SLEEP: float = 0.05
# Maximum sleep between acquire retries (seconds)
_MAX_RETRY_SLEEP: float = 2.0


class RedisRateLimiter:
    """
    Distributed token-bucket rate limiter backed by Redis.

    All workers across all processes share a single token bucket keyed by
    ``key``. Tokens are replenished at ``rate_per_minute / 60`` per second.
    ``acquire()`` blocks (with async sleep) until a token is available.

    Implementation uses a Lua script for atomic check-and-decrement so that
    concurrent workers never over-consume tokens.
    """

    # Lua script: atomically check remaining tokens and consume one.
    # Returns the number of seconds to wait (0 means token acquired).
    _LUA_SCRIPT: str = """
    local key = KEYS[1]
    local max_tokens = tonumber(ARGV[1])
    local refill_rate = tonumber(ARGV[2])  -- tokens per second
    local now = tonumber(ARGV[3])          -- current timestamp (float)

    local data = redis.call('HMGET', key, 'tokens', 'last_refill')
    local tokens = tonumber(data[1])
    local last_refill = tonumber(data[2])

    if tokens == nil then
        -- First call: initialize bucket
        tokens = max_tokens
        last_refill = now
    end

    -- Refill tokens based on elapsed time
    local elapsed = now - last_refill
    if elapsed > 0 then
        local new_tokens = elapsed * refill_rate
        tokens = math.min(max_tokens, tokens + new_tokens)
        last_refill = now
    end

    if tokens >= 1 then
        -- Consume a token
        tokens = tokens - 1
        redis.call('HMSET', key, 'tokens', tostring(tokens), 'last_refill', tostring(last_refill))
        redis.call('EXPIRE', key, 3600)  -- Auto-expire after 1 hour of inactivity
        return 0  -- success, no wait
    else
        -- No tokens: return seconds until next token
        redis.call('HMSET', key, 'tokens', tostring(tokens), 'last_refill', tostring(last_refill))
        redis.call('EXPIRE', key, 3600)
        local wait = (1 - tokens) / refill_rate
        return wait
    end
    """

    def __init__(
        self,
        redis_url: str,
        key: str,
        rate_per_minute: int,
        *,
        burst: Optional[int] = None,
    ) -> None:
        """
        Args:
            redis_url: Redis connection URL.
            key: Unique key for this rate limiter bucket (e.g., "bulk_op:abc:perplexity").
            rate_per_minute: Maximum requests per minute.
            burst: Maximum burst size (defaults to rate_per_minute / 6, i.e. 10 seconds worth).
        """
        self._redis: aioredis.Redis = aioredis.from_url(
            redis_url, decode_responses=True
        )
        self._key: str = f"rate_limit:{key}"
        self._rate_per_minute: int = rate_per_minute
        self._refill_rate: float = rate_per_minute / 60.0  # tokens per second
        self._max_tokens: int = burst if burst is not None else max(1, rate_per_minute // 6)
        self._script: Optional[object] = None

    async def _get_script(self) -> object:
        """Lazily register the Lua script."""
        if self._script is None:
            self._script = self._redis.register_script(self._LUA_SCRIPT)
        return self._script

    async def acquire(self, timeout: float = 120.0) -> bool:
        """
        Block until a rate limit token is available.

        Args:
            timeout: Maximum seconds to wait before giving up.

        Returns:
            True if token acquired, False if timed out.
        """
        script = await self._get_script()
        deadline: float = time.monotonic() + timeout

        while True:
            now: float = time.time()
            wait_seconds: float = await script(
                keys=[self._key],
                args=[self._max_tokens, self._refill_rate, now],
            )
            wait_seconds = float(wait_seconds)

            if wait_seconds <= 0:
                return True  # Token acquired

            # Check timeout
            remaining: float = deadline - time.monotonic()
            if remaining <= 0:
                logger.warning(
                    "[RateLimiter] Timed out waiting for token on key=%s",
                    self._key,
                )
                return False

            # Sleep for the suggested wait time (clamped)
            sleep_time: float = min(
                max(wait_seconds, _MIN_RETRY_SLEEP),
                min(_MAX_RETRY_SLEEP, remaining),
            )
            await asyncio.sleep(sleep_time)

    async def close(self) -> None:
        """Close the Redis connection."""
        await self._redis.close()
