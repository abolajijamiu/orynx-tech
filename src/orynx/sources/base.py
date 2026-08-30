"""The contract every platform adapter implements.

Adapters differ enormously — a JSON API, a paginated catalogue, a review index —
but they all reduce to the same job: yield `RawBook` records. Everything
downstream (dedupe, scoring, export) is written against `RawBook` alone, so a new
platform never touches the pipeline.
"""

from __future__ import annotations

import hashlib
import json
from abc import ABC
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass, field
from typing import Any

from orynx.fetch import PoliteClient


@dataclass(slots=True)
class RawAuthor:
    """An author exactly as one platform reported them."""

    name: str
    role: str = "author"
    url: str | None = None
    bio: str | None = None
    email: str | None = None
    website: str | None = None
    socials: dict[str, str] = field(default_factory=dict)
    external_id: str | None = None


@dataclass(slots=True)
class RawBook:
    """A book exactly as one platform reported it, before normalisation."""

    source_id: str
    title: str
    authors: list[RawAuthor] = field(default_factory=list)
    subtitle: str | None = None
    external_id: str | None = None
    url: str | None = None
    isbn13: str | None = None
    isbn10: str | None = None
    published_date: str | None = None
    publisher: str | None = None
    language: str | None = None
    page_count: int | None = None
    description: str | None = None
    cover_url: str | None = None
    categories: list[str] = field(default_factory=list)
    ratings_count: int | None = None
    average_rating: float | None = None
    price: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def content_hash(self) -> str:
        """Stable digest used to skip unchanged records on re-crawl."""
        payload = asdict(self)
        payload.pop("raw", None)
        blob = json.dumps(payload, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SourceMeta:
    id: str
    name: str
    kind: str
    homepage: str | None = None
    trust: float = 0.5
    requires_api_key: bool = False
    notes: str | None = None


class BaseSource(ABC):
    """Adapters subclass this and implement `search` or `crawl`, or both."""

    meta: SourceMeta

    def __init__(self, client: PoliteClient) -> None:
        self.client = client

    @property
    def id(self) -> str:
        return self.meta.id

    async def search(self, query: str, *, limit: int = 100) -> AsyncIterator[RawBook]:
        """Keyword search. Adapters over a search API override this."""
        raise NotImplementedError(f"{self.id} does not support keyword search")
        yield  # pragma: no cover  (marks this a generator for type checkers)

    async def crawl(self, *, limit: int | None = None) -> AsyncIterator[RawBook]:
        """Walk the platform's catalogue. Recipe-driven sites override this."""
        raise NotImplementedError(f"{self.id} does not support catalogue crawling")
        yield  # pragma: no cover

    @property
    def supports_search(self) -> bool:
        return type(self).search is not BaseSource.search

    @property
    def supports_crawl(self) -> bool:
        return type(self).crawl is not BaseSource.crawl
