"""The single HTTP entry point for every source.

Routing all traffic through one client is what makes politeness enforceable:
robots.txt, per-domain pacing, retries with backoff, and caching cannot be
forgotten by an individual adapter because adapters never touch httpx directly.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx

from orynx.config import Settings, get_settings
from orynx.fetch.cache import HttpCache
from orynx.fetch.ratelimit import DomainRateLimiter
from orynx.fetch.robots import RobotsCache
from orynx.logging import get_logger

log = get_logger(__name__)

RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}

# Consecutive connection failures before a host is written off for this run.
CIRCUIT_BREAK_AFTER = 3


class RobotsDenied(Exception):
    """Raised when robots.txt disallows a URL and the crawl obeys robots."""


class HostUnavailable(Exception):
    """Raised when a host has failed repeatedly and is being skipped.

    Without this, a blocked or dead host is retried for every record in a run;
    across thousands of authors that is hours spent waiting on timeouts.
    """


@dataclass
class FetchResult:
    url: str
    status: int
    text: str
    headers: dict[str, str] = field(default_factory=dict)
    from_cache: bool = False

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def json(self) -> Any:
        import json

        return json.loads(self.text)


class PoliteClient:
    """Async HTTP client with robots, rate limiting, retries and caching."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        use_cache: bool = True,
        obey_robots: bool | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.obey_robots = self.settings.obey_robots if obey_robots is None else obey_robots
        headers = {
            "User-Agent": self.settings.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
            "Accept-Language": "en",
        }
        if self.settings.contact_email:
            headers["From"] = self.settings.contact_email
        self._client = httpx.AsyncClient(
            headers=headers,
            timeout=self.settings.request_timeout,
            follow_redirects=True,
            transport=transport,
        )
        self._robots = RobotsCache(self.settings.user_agent)
        self._limiter = DomainRateLimiter(self.settings.default_rate_limit_rps)
        self._cache = HttpCache(
            self.settings.cache_dir, self.settings.cache_ttl_seconds, enabled=use_cache
        )
        self._semaphore = asyncio.Semaphore(self.settings.default_concurrency)
        self._consecutive_failures: dict[str, int] = {}
        self._dead_domains: set[str] = set()
        self.stats: dict[str, int] = {
            "requests": 0, "cache_hits": 0, "errors": 0, "denied": 0, "skipped_dead": 0,
        }

    async def __aenter__(self) -> PoliteClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    def configure_domain(self, domain: str, rps: float) -> None:
        self._limiter.configure(domain, rps)

    @staticmethod
    def domain_of(url: str) -> str:
        return urlsplit(url).netloc.lower()

    async def get(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        force_refresh: bool = False,
        obey_robots: bool | None = None,
    ) -> FetchResult:
        request = self._client.build_request("GET", url, params=params)
        full_url = str(request.url)

        if not force_refresh:
            cached = self._cache.get(full_url)
            if cached is not None:
                self.stats["cache_hits"] += 1
                return FetchResult(
                    url=full_url,
                    status=cached["status"],
                    text=cached["text"],
                    headers=cached.get("headers", {}),
                    from_cache=True,
                )

        domain_key = self.domain_of(full_url)
        if domain_key in self._dead_domains:
            self.stats["skipped_dead"] += 1
            raise HostUnavailable(
                f"{domain_key} failed {CIRCUIT_BREAK_AFTER} times; skipping for this run"
            )

        should_obey = self.obey_robots if obey_robots is None else obey_robots
        if should_obey and not await self._robots.allowed(self._client, full_url):
            self.stats["denied"] += 1
            raise RobotsDenied(f"robots.txt disallows {full_url}")

        domain = self.domain_of(full_url)
        # A site that publishes Crawl-delay is asking for a specific pace; honour
        # it whenever it is slower than our configured default.
        delay = await self._robots.crawl_delay(self._client, full_url) if should_obey else None
        if delay:
            self._limiter.configure(domain, min(self.settings.default_rate_limit_rps, 1.0 / delay))

        result = await self._request_with_retries(full_url, domain)
        if result.ok:
            self._cache.set(result.url, result.status, result.text, result.headers)
        return result

    async def _request_with_retries(self, url: str, domain: str) -> FetchResult:
        last_error: Exception | None = None
        for attempt in range(self.settings.max_retries + 1):
            await self._limiter.acquire(domain)
            async with self._semaphore:
                try:
                    self.stats["requests"] += 1
                    resp = await self._client.get(url)
                except httpx.HTTPError as exc:
                    last_error = exc
                    self.stats["errors"] += 1
                    if attempt == self.settings.max_retries:
                        break
                    await asyncio.sleep(2**attempt)
                    continue

            # A response of any kind means the host is alive.
            self._consecutive_failures[domain] = 0

            if resp.status_code in RETRYABLE_STATUS and attempt < self.settings.max_retries:
                backoff = self._retry_after(resp) or 2**attempt
                self._limiter.penalise(domain, backoff)
                log.debug("retrying %s after %s (%.1fs)", url, resp.status_code, backoff)
                await asyncio.sleep(backoff)
                continue

            return FetchResult(
                url=str(resp.url),
                status=resp.status_code,
                text=resp.text,
                headers=dict(resp.headers),
            )

        failures = self._consecutive_failures.get(domain, 0) + 1
        self._consecutive_failures[domain] = failures
        if failures >= CIRCUIT_BREAK_AFTER:
            log.warning(
                "%s failed %s times in a row; skipping it for the rest of this run",
                domain,
                failures,
            )
            self._dead_domains.add(domain)

        raise httpx.HTTPError(f"GET {url} failed after retries: {last_error}")

    @staticmethod
    def _retry_after(resp: httpx.Response) -> float | None:
        value = resp.headers.get("Retry-After")
        if not value:
            return None
        try:
            return float(value)
        except ValueError:
            return None
