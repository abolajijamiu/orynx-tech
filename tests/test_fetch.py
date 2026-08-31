"""Politeness layer: robots, retries, caching, rate limiting."""

from __future__ import annotations

import httpx
import pytest

from orynx.fetch import PoliteClient, RobotsDenied
from orynx.fetch.cache import HttpCache
from orynx.fetch.ratelimit import DomainRateLimiter


@pytest.mark.asyncio
async def test_robots_disallow_blocks_the_request(settings):
    robots = "User-agent: *\nDisallow: /private/\n"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=robots)
        return httpx.Response(200, text="body")

    client = PoliteClient(
        settings=settings, use_cache=False, obey_robots=True,
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(RobotsDenied):
            await client.get("https://press.test/private/x")
        allowed = await client.get("https://press.test/public/x")
        assert allowed.ok
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_missing_robots_txt_allows_the_crawl(settings):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, text="body")

    client = PoliteClient(
        settings=settings, use_cache=False, obey_robots=True,
        transport=httpx.MockTransport(handler),
    )
    try:
        assert (await client.get("https://press.test/x")).ok
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_retries_then_succeeds(settings):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, headers={"Retry-After": "0"})
        return httpx.Response(200, text="ok")

    client = PoliteClient(
        settings=settings, use_cache=False, obey_robots=False,
        transport=httpx.MockTransport(handler),
    )
    try:
        result = await client.get("https://press.test/x")
        assert result.ok
        assert calls["n"] == 2
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_cache_prevents_a_second_network_call(settings):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, text="cached body")

    client = PoliteClient(
        settings=settings, use_cache=True, obey_robots=False,
        transport=httpx.MockTransport(handler),
    )
    try:
        first = await client.get("https://press.test/x")
        second = await client.get("https://press.test/x")
        assert calls["n"] == 1
        assert not first.from_cache
        assert second.from_cache
        assert second.text == "cached body"
    finally:
        await client.aclose()


def test_cache_entries_expire(tmp_path):
    cache = HttpCache(tmp_path, ttl_seconds=0)
    cache.set("https://x.test/a", 200, "body", {})
    assert cache.get("https://x.test/a") is None


def test_cache_survives_a_corrupt_entry(tmp_path):
    cache = HttpCache(tmp_path, ttl_seconds=999)
    cache.set("https://x.test/a", 200, "body", {})
    path = cache._path("https://x.test/a")
    path.write_text("{ not json", encoding="utf-8")
    assert cache.get("https://x.test/a") is None


@pytest.mark.asyncio
async def test_rate_limiter_serialises_per_domain():
    import time

    limiter = DomainRateLimiter(default_rps=50.0)  # 20ms apart
    start = time.monotonic()
    for _ in range(3):
        await limiter.acquire("press.test")
    assert time.monotonic() - start >= 0.03


def test_domain_of_is_case_insensitive():
    assert PoliteClient.domain_of("https://PRESS.Test/x") == "press.test"


@pytest.mark.asyncio
async def test_circuit_breaker_stops_hammering_a_dead_host(settings):
    """A blocked or down host must not be retried once per record."""
    from orynx.fetch.client import CIRCUIT_BREAK_AFTER, HostUnavailable

    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ConnectError("refused")

    client = PoliteClient(
        settings=settings, use_cache=False, obey_robots=False,
        transport=httpx.MockTransport(handler),
    )
    try:
        for _ in range(CIRCUIT_BREAK_AFTER):
            with pytest.raises(httpx.HTTPError):
                await client.get("https://dead.test/a")
        attempts_before = calls["n"]

        # Once broken, further requests fail immediately without touching the network.
        with pytest.raises(HostUnavailable):
            await client.get("https://dead.test/b")
        assert calls["n"] == attempts_before
        assert client.stats["skipped_dead"] == 1
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_circuit_breaker_is_per_domain(settings):
    from orynx.fetch.client import CIRCUIT_BREAK_AFTER, HostUnavailable

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "dead.test":
            raise httpx.ConnectError("refused")
        return httpx.Response(200, text="alive")

    client = PoliteClient(
        settings=settings, use_cache=False, obey_robots=False,
        transport=httpx.MockTransport(handler),
    )
    try:
        for _ in range(CIRCUIT_BREAK_AFTER):
            with pytest.raises(httpx.HTTPError):
                await client.get("https://dead.test/a")
        with pytest.raises(HostUnavailable):
            await client.get("https://dead.test/b")
        # A healthy host is unaffected.
        assert (await client.get("https://alive.test/x")).ok
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_a_recovered_host_resets_its_failure_count(settings):
    state = {"fail": True}

    def handler(request: httpx.Request) -> httpx.Response:
        if state["fail"]:
            raise httpx.ConnectError("refused")
        return httpx.Response(200, text="ok")

    client = PoliteClient(
        settings=settings, use_cache=False, obey_robots=False,
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(httpx.HTTPError):
            await client.get("https://flaky.test/a")
        state["fail"] = False
        assert (await client.get("https://flaky.test/b")).ok
        state["fail"] = True
        # The earlier failure was forgotten, so this one starts a fresh count.
        with pytest.raises(httpx.HTTPError):
            await client.get("https://flaky.test/c")
        assert "flaky.test" not in client._dead_domains
    finally:
        await client.aclose()
