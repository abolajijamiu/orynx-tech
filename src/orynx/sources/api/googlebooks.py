"""Google Books — good commercial metadata and publisher attribution.

Works without a key at low volume; set ORYNX_GOOGLE_BOOKS_API_KEY for headroom.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from orynx.sources.base import BaseSource, RawAuthor, RawBook, SourceMeta
from orynx.textutil import clean_text, split_isbns

SEARCH_URL = "https://www.googleapis.com/books/v1/volumes"
PAGE_SIZE = 40  # API maximum


class GoogleBooksSource(BaseSource):
    meta = SourceMeta(
        id="googlebooks",
        name="Google Books",
        kind="bibliographic",
        homepage="https://books.google.com",
        trust=0.8,
        notes="Key optional. Publisher field is the main signal for imprint targeting.",
    )

    async def search(self, query: str, *, limit: int = 100) -> AsyncIterator[RawBook]:
        yielded = 0
        start_index = 0
        while yielded < limit:
            params: dict[str, Any] = {
                "q": query,
                "startIndex": start_index,
                "maxResults": min(PAGE_SIZE, limit - yielded),
                "printType": "books",
            }
            key = self.client.settings.google_books_api_key
            if key:
                params["key"] = key
            result = await self.client.get(SEARCH_URL, params=params)
            if not result.ok:
                break
            payload = result.json()
            items = payload.get("items") or []
            if not items:
                break
            for item in items:
                yield self._to_book(item)
                yielded += 1
                if yielded >= limit:
                    return
            start_index += len(items)
            if start_index >= int(payload.get("totalItems", 0)):
                break

    def _to_book(self, item: dict[str, Any]) -> RawBook:
        info = item.get("volumeInfo") or {}
        identifiers = [i.get("identifier", "") for i in info.get("industryIdentifiers") or []]
        isbn13, isbn10 = split_isbns(identifiers)
        return RawBook(
            source_id=self.id,
            external_id=item.get("id"),
            url=info.get("infoLink") or info.get("canonicalVolumeLink"),
            title=clean_text(info.get("title")) or "",
            subtitle=clean_text(info.get("subtitle")),
            authors=[RawAuthor(name=n) for n in info.get("authors") or []],
            isbn13=isbn13,
            isbn10=isbn10,
            published_date=info.get("publishedDate"),
            publisher=clean_text(info.get("publisher")),
            language=info.get("language"),
            page_count=info.get("pageCount"),
            description=clean_text(info.get("description")),
            cover_url=(info.get("imageLinks") or {}).get("thumbnail"),
            categories=info.get("categories") or [],
            ratings_count=info.get("ratingsCount"),
            average_rating=info.get("averageRating"),
            raw=item,
        )
