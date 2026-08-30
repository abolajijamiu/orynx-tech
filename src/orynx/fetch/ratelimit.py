"""Per-domain pacing.

Rate limits are tracked per registrable domain rather than per source, so two
recipes pointing at the same host cannot combine into double the traffic.
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict


class DomainRateLimiter:
    """Serialises requests per domain and spaces them by `1 / rps`."""

    def __init__(self, default_rps: float = 0.5) -> None:
        self.default_rps = default_rps
        self._rps: dict[str, float] = {}
        self._next_allowed: dict[str, float] = defaultdict(float)
        self._locks: dict[str, asyncio.Lock] = {}

    def configure(self, domain: str, rps: float) -> None:
        self._rps[domain] = rps

    def _lock(self, domain: str) -> asyncio.Lock:
        if domain not in self._locks:
            self._locks[domain] = asyncio.Lock()
        return self._locks[domain]

    async def acquire(self, domain: str) -> None:
        rps = self._rps.get(domain, self.default_rps)
        interval = 1.0 / rps if rps > 0 else 0.0
        async with self._lock(domain):
            now = time.monotonic()
            wait = self._next_allowed[domain] - now
            if wait > 0:
                await asyncio.sleep(wait)
                now = time.monotonic()
            self._next_allowed[domain] = now + interval

    def penalise(self, domain: str, seconds: float) -> None:
        """Back off after a 429 or 503, on top of the normal interval."""
        self._next_allowed[domain] = max(
            self._next_allowed[domain], time.monotonic() + seconds
        )
