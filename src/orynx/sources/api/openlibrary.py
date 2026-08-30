"""Open Library — free, no key, and the broadest bibliographic coverage.

Useful as the spine of a lead list: it carries ratings counts, which is the
cheapest available signal for "published but invisible".
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from orynx.sources.base import BaseSource, RawAuthor, RawBook, SourceMeta
from orynx.textutil import clean_text, split_isbns

SEARCH_URL = "https://openlibrary.org/search.json"
PAGE_SIZE = 100

# Requesting explicit fields keeps responses an order of magnitude smaller.
FIELDS = ",".join(
    [
        "key", "title", "subtitle", "author_name", "author_key", "isbn",
        "first_publish_year", "publish_date", "publisher", "language",
        "number_of_pages_median", "cover_i", "subject", "ratings_count",
        "ratings_average", "edition_count",
    ]
)


class OpenLibrarySource(BaseSource):
    meta = SourceMeta(
        id="openlibrary",
        name="Open Library",
        kind="bibliographic",
        homepage="https://openlibrary.org",
        trust=0.85,
        notes="Public API, no key required. Ratings counts support visibility scoring.",
    )

    async def search(self, query: str, *, limit: int = 100) -> AsyncIterator[RawBook]:
        yielded = 0
        offset = 0
        while yielded < limit:
            page_size = min(PAGE_SIZE, limit - yielded)
            result = await self.client.get(
                SEARCH_URL,
                params={"q": query, "limit": page_size, "offset": offset, "fields": FIELDS},
            )
            if not result.ok:
                break
            payload = result.json()
            docs = payload.get("docs") or []
            if not docs:
                break
            for doc in docs:
                yield self._to_book(doc)
                yielded += 1
                if yielded >= limit:
                    return
            offset += len(docs)
            if offset >= int(payload.get("numFound", 0)):
                break

    def _to_book(self, doc: dict[str, Any]) -> RawBook:
        isbn13, isbn10 = split_isbns(doc.get("isbn"))
        names = doc.get("author_name") or []
        keys = doc.get("author_key") or []
        authors = [
            RawAuthor(
                name=name,
                external_id=keys[i] if i < len(keys) else None,
                url=f"https://openlibrary.org/authors/{keys[i]}" if i < len(keys) else None,
            )
            for i, name in enumerate(names)
        ]
        publishers = doc.get("publisher") or []
        languages = doc.get("language") or []
        published = doc.get("first_publish_year")
        key = doc.get("key", "")
        return RawBook(
            source_id=self.id,
            external_id=key,
            url=f"https://openlibrary.org{key}" if key else None,
            title=clean_text(doc.get("title")) or "",
            subtitle=clean_text(doc.get("subtitle")),
            authors=authors,
            isbn13=isbn13,
            isbn10=isbn10,
            published_date=str(published) if published else None,
            publisher=clean_text(publishers[0]) if publishers else None,
            language=languages[0] if languages else None,
            page_count=doc.get("number_of_pages_median"),
            cover_url=(
                f"https://covers.openlibrary.org/b/id/{doc['cover_i']}-L.jpg"
                if doc.get("cover_i")
                else None
            ),
            categories=[c for c in (doc.get("subject") or [])[:12]],
            ratings_count=doc.get("ratings_count"),
            average_rating=doc.get("ratings_average"),
            raw=doc,
        )
