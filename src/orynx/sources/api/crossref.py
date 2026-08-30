"""Crossref — scholarly monographs, with author affiliations and ORCIDs.

Academic authors are reachable through their institution, which makes these
leads unusually contactable compared with trade fiction.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from orynx.sources.base import BaseSource, RawAuthor, RawBook, SourceMeta
from orynx.textutil import clean_text, split_isbns

SEARCH_URL = "https://api.crossref.org/works"
PAGE_SIZE = 100


class CrossrefSource(BaseSource):
    meta = SourceMeta(
        id="crossref",
        name="Crossref",
        kind="bibliographic",
        homepage="https://www.crossref.org",
        trust=0.9,
        notes="Books and chapters. Sending a contact email raises your rate limit tier.",
    )

    async def search(self, query: str, *, limit: int = 100) -> AsyncIterator[RawBook]:
        yielded = 0
        offset = 0
        while yielded < limit:
            params: dict[str, Any] = {
                "query": query,
                "filter": "type:book,type:monograph",
                "rows": min(PAGE_SIZE, limit - yielded),
                "offset": offset,
            }
            email = self.client.settings.contact_email
            if email:
                # Crossref routes requests with a mailto into their faster pool.
                params["mailto"] = email
            result = await self.client.get(SEARCH_URL, params=params)
            if not result.ok:
                break
            message = result.json().get("message") or {}
            items = message.get("items") or []
            if not items:
                break
            for item in items:
                yield self._to_book(item)
                yielded += 1
                if yielded >= limit:
                    return
            offset += len(items)
            if offset >= int(message.get("total-results", 0)):
                break

    def _to_book(self, item: dict[str, Any]) -> RawBook:
        titles = item.get("title") or []
        subtitles = item.get("subtitle") or []
        isbn13, isbn10 = split_isbns(item.get("ISBN"))
        authors = []
        for person in item.get("author") or []:
            name = " ".join(
                p for p in [person.get("given"), person.get("family")] if p
            ).strip() or person.get("name")
            if not name:
                continue
            affiliations = [a.get("name") for a in person.get("affiliation") or [] if a.get("name")]
            authors.append(
                RawAuthor(
                    name=name,
                    external_id=person.get("ORCID"),
                    url=person.get("ORCID"),
                    bio="; ".join(affiliations) or None,
                )
            )
        return RawBook(
            source_id=self.id,
            external_id=item.get("DOI"),
            url=item.get("URL"),
            title=clean_text(titles[0]) if titles else "",
            subtitle=clean_text(subtitles[0]) if subtitles else None,
            authors=authors,
            isbn13=isbn13,
            isbn10=isbn10,
            published_date=_issued_date(item),
            publisher=clean_text(item.get("publisher")),
            language=item.get("language"),
            description=clean_text(item.get("abstract")),
            categories=item.get("subject") or [],
            raw=item,
        )


def _issued_date(item: dict[str, Any]) -> str | None:
    parts = (item.get("issued") or {}).get("date-parts") or []
    if not parts or not parts[0]:
        return None
    fragments = [str(p) for p in parts[0] if p is not None]
    return "-".join(fragments) if fragments else None
