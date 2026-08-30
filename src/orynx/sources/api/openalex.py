"""OpenAlex — open scholarly graph, with institution and country per author."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from orynx.sources.base import BaseSource, RawAuthor, RawBook, SourceMeta
from orynx.textutil import clean_text

SEARCH_URL = "https://api.openalex.org/works"
PAGE_SIZE = 100


class OpenAlexSource(BaseSource):
    meta = SourceMeta(
        id="openalex",
        name="OpenAlex",
        kind="bibliographic",
        homepage="https://openalex.org",
        trust=0.85,
        notes="Free. Affiliation and country make academic leads segmentable by region.",
    )

    async def search(self, query: str, *, limit: int = 100) -> AsyncIterator[RawBook]:
        yielded = 0
        page = 1
        while yielded < limit:
            params: dict[str, Any] = {
                "search": query,
                "filter": "type:book|book-chapter",
                "per-page": min(PAGE_SIZE, limit - yielded),
                "page": page,
            }
            email = self.client.settings.contact_email
            if email:
                params["mailto"] = email
            result = await self.client.get(SEARCH_URL, params=params)
            if not result.ok:
                break
            payload = result.json()
            results = payload.get("results") or []
            if not results:
                break
            for item in results:
                yield self._to_book(item)
                yielded += 1
                if yielded >= limit:
                    return
            page += 1
            if page > 50:  # OpenAlex caps basic paging; switch to cursor beyond this.
                break

    def _to_book(self, item: dict[str, Any]) -> RawBook:
        authors = []
        for authorship in item.get("authorships") or []:
            person = authorship.get("author") or {}
            name = person.get("display_name")
            if not name:
                continue
            institutions = [
                i.get("display_name") for i in authorship.get("institutions") or []
                if i.get("display_name")
            ]
            authors.append(
                RawAuthor(
                    name=name,
                    external_id=person.get("id"),
                    url=person.get("orcid") or person.get("id"),
                    bio="; ".join(institutions) or None,
                )
            )
        location = item.get("primary_location") or {}
        venue = location.get("source") or {}
        concepts = [
            c.get("display_name")
            for c in item.get("concepts") or []
            if c.get("display_name")
        ]
        return RawBook(
            source_id=self.id,
            external_id=item.get("id"),
            url=item.get("doi") or location.get("landing_page_url") or item.get("id"),
            title=clean_text(item.get("display_name") or item.get("title")) or "",
            authors=authors,
            published_date=item.get("publication_date") or str(item.get("publication_year") or ""),
            publisher=clean_text(venue.get("host_organization_name") or venue.get("display_name")),
            language=item.get("language"),
            categories=concepts[:12],
            ratings_count=item.get("cited_by_count"),
            raw=item,
        )
