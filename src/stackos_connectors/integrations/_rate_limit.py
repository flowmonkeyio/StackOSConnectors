"""Caller-owned token buckets. Applications choose the sharing scope."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass


@dataclass
class TokenBucket:
    """Classic token bucket: refill rate ``qps`` tokens/sec, capacity ``qps``.

    ``acquire(n=1)`` blocks asynchronously until ``n`` tokens are
    available; the wait is computed deterministically rather than
    polling, so we don't burn CPU during the wait. Tests use
    ``acquire_now`` to peek without sleeping.
    """

    qps: float
    capacity: float
    tokens: float
    last: float

    @classmethod
    def for_qps(cls, qps: float, *, capacity: float | None = None) -> TokenBucket:
        """Build a bucket with sensible defaults."""
        cap = capacity if capacity is not None else max(1.0, qps)
        return cls(qps=qps, capacity=cap, tokens=cap, last=time.monotonic())

    def _refill(self, *, now: float | None = None) -> None:
        """Add tokens earned since the last call."""
        ts = now if now is not None else time.monotonic()
        elapsed = ts - self.last
        if elapsed > 0:
            self.tokens = min(self.capacity, self.tokens + elapsed * self.qps)
            self.last = ts

    def try_acquire(self, n: float = 1.0, *, now: float | None = None) -> bool:
        """Take ``n`` tokens if available; return ``False`` if not.

        Used by tests + the doctor probe. Production code calls
        ``acquire`` which awaits.
        """
        self._refill(now=now)
        if self.tokens >= n:
            self.tokens -= n
            return True
        return False

    async def acquire(self, n: float = 1.0) -> None:
        """Block asynchronously until ``n`` tokens are available."""
        if n <= 0:
            return
        if n > self.capacity:
            # Either the caller asked for more than the bucket can ever
            # hold (programmer error) or qps was set absurdly low. We
            # defer to the underlying integration to surface the failure
            # rather than swallowing it here.
            raise ValueError(f"requested {n} tokens but capacity is {self.capacity}")
        while True:
            self._refill()
            if self.tokens >= n:
                self.tokens -= n
                return
            deficit = n - self.tokens
            wait = deficit / self.qps if self.qps > 0 else 1.0
            # 50ms minimum so we don't busy-loop if qps is huge.
            await asyncio.sleep(max(wait, 0.05))


__all__ = ["TokenBucket"]
