"""robots.txt evaluation, cached per host for the life of a crawl."""

from __future__ import annotations

import asyncio
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from orynx.logging import get_logger

log = get_logger(__name__)


class RobotsCache:
    def __init__(self, user_agent: str) -> None:
        self.user_agent = user_agent
        self._parsers: dict[str, RobotFileParser | None] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, origin: str) -> asyncio.Lock:
        if origin not in self._locks:
            self._locks[origin] = asyncio.Lock()
        return self._locks[origin]

    async def _load(self, client: httpx.AsyncClient, origin: str) -> RobotFileParser | None:
        parser = RobotFileParser()
        url = f"{origin}/robots.txt"
        try:
            resp = await client.get(url, timeout=15.0)
        except httpx.HTTPError as exc:
            # An unreachable robots.txt is not permission; but neither is it a
            # directive. Convention is to allow, and our rate limits still apply.
            log.debug("robots.txt unreachable for %s (%s); allowing", origin, exc)
            return None
        if resp.status_code >= 400:
            log.debug("robots.txt %s for %s; allowing", resp.status_code, origin)
            return None
        parser.parse(resp.text.splitlines())
        return parser

    async def allowed(self, client: httpx.AsyncClient, url: str) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        async with self._lock(origin):
            if origin not in self._parsers:
                self._parsers[origin] = await self._load(client, origin)
        parser = self._parsers[origin]
        if parser is None:
            return True
        return parser.can_fetch(self.user_agent, url)

    async def crawl_delay(self, client: httpx.AsyncClient, url: str) -> float | None:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        async with self._lock(origin):
            if origin not in self._parsers:
                self._parsers[origin] = await self._load(client, origin)
        parser = self._parsers[origin]
        if parser is None:
            return None
        try:
            delay = parser.crawl_delay(self.user_agent)
        except Exception:
            return None
        return float(delay) if delay else None
