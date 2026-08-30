"""A source driven entirely by a YAML recipe.

One class serves every HTML platform. Discovery walks listing pages, the listing
selector yields detail links, and each detail page is read via JSON-LD where it
exists and CSS selectors where it does not.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urljoin

from selectolax.parser import HTMLParser

from orynx.fetch import PoliteClient, RobotsDenied
from orynx.logging import get_logger
from orynx.sources.base import BaseSource, RawAuthor, RawBook, SourceMeta
from orynx.sources.html.extract import (
    book_fields_from_jsonld,
    extract_field,
    find_book_jsonld,
)
from orynx.sources.html.recipe import Recipe
from orynx.textutil import clean_text, normalize_isbn

log = get_logger(__name__)

_SITEMAP_LOC = re.compile(r"<loc>\s*([^<]+?)\s*</loc>", re.IGNORECASE)


class RecipeSource(BaseSource):
    """Adapter whose behaviour comes from a `Recipe` rather than from code."""

    def __init__(self, client: PoliteClient, recipe: Recipe) -> None:
        super().__init__(client)
        self.recipe = recipe
        self.meta = SourceMeta(
            id=recipe.id,
            name=recipe.name,
            kind=recipe.kind,
            homepage=recipe.homepage,
            trust=recipe.trust,
            notes=recipe.permitted_note,
        )
        if recipe.politeness.rate_limit_rps and recipe.homepage:
            client.configure_domain(
                PoliteClient.domain_of(recipe.homepage), recipe.politeness.rate_limit_rps
            )

    async def crawl(self, *, limit: int | None = None) -> AsyncIterator[RawBook]:
        if not self.recipe.permitted:
            log.warning(
                "recipe %s is marked not permitted (%s); skipping",
                self.recipe.id,
                self.recipe.permitted_note or "no reason recorded",
            )
            return

        seen: set[str] = set()
        emitted = 0

        if self._sitemap_is_direct():
            async for book in self._crawl_sitemap_details(seen, limit):
                yield book
            return

        async for page_url, html in self._listing_pages():
            detail_urls = self._detail_links(html, page_url)
            if not detail_urls and self.recipe.discover.stop_when_empty:
                log.debug("no items on %s; ending discovery", page_url)
                break

            for detail_url, listing_fields in detail_urls:
                if detail_url in seen:
                    continue
                seen.add(detail_url)

                book = await self._build_book(detail_url, listing_fields)
                if book is None or not book.title:
                    continue
                yield book
                emitted += 1
                if limit is not None and emitted >= limit:
                    return

    def _sitemap_is_direct(self) -> bool:
        discover = self.recipe.discover
        return discover.strategy == "sitemap" and discover.sitemap_yields == "detail"

    async def _crawl_sitemap_details(
        self, seen: set[str], limit: int | None
    ) -> AsyncIterator[RawBook]:
        """Treat every sitemap entry as a book page, skipping the listing step."""
        discover = self.recipe.discover
        index = await self._get_text(discover.sitemap_url or "", self.recipe.politeness.obey_robots)
        if not index:
            return
        urls = self._sitemap_urls(index)

        # A sitemap index points at more sitemaps; follow one level down.
        if urls and all(u.endswith((".xml", ".xml.gz")) for u in urls[:5]):
            nested: list[str] = []
            for child in urls[:20]:
                child_xml = await self._get_text(child, self.recipe.politeness.obey_robots)
                if child_xml:
                    nested.extend(self._sitemap_urls(child_xml))
            urls = nested

        emitted = 0
        for url in urls[: discover.max_urls]:
            if url in seen:
                continue
            seen.add(url)
            book = await self._build_book(url, {})
            if book is None or not book.title:
                continue
            yield book
            emitted += 1
            if limit is not None and emitted >= limit:
                return

    async def _listing_pages(self) -> AsyncIterator[tuple[str, str]]:
        discover = self.recipe.discover
        obey = self.recipe.politeness.obey_robots

        if discover.strategy == "static":
            for url in discover.urls:
                html = await self._get_text(url, obey)
                if html:
                    yield url, html
            return

        if discover.strategy == "sitemap":
            index = await self._get_text(discover.sitemap_url or "", obey)
            if not index:
                return
            for url in self._sitemap_urls(index)[: discover.max_pages]:
                html = await self._get_text(url, obey)
                if html:
                    yield url, html
            return

        page = discover.start_page
        for _ in range(discover.max_pages):
            url = discover.url_template.format(page=page)  # type: ignore[union-attr]
            html = await self._get_text(url, obey)
            if not html:
                return
            yield url, html
            page += discover.page_step

    def _sitemap_urls(self, xml: str) -> list[str]:
        urls = [u.strip() for u in _SITEMAP_LOC.findall(xml)]
        pattern = self.recipe.discover.url_pattern
        if pattern:
            # Nested sitemap references must survive the filter so they can be
            # followed; the pattern is meant for the page URLs inside them.
            urls = [u for u in urls if pattern in u or u.endswith((".xml", ".xml.gz"))]
        return urls

    async def _get_text(self, url: str, obey_robots: bool) -> str | None:
        if not url:
            return None
        try:
            result = await self.client.get(url, obey_robots=obey_robots)
        except RobotsDenied as exc:
            log.warning("%s: %s", self.recipe.id, exc)
            return None
        except Exception as exc:  # network failures should not kill a whole crawl
            log.warning("%s: fetch failed for %s (%s)", self.recipe.id, url, exc)
            return None
        if not result.ok:
            log.debug("%s: %s returned %s", self.recipe.id, url, result.status)
            return None
        return result.text

    def _detail_links(self, html: str, page_url: str) -> list[tuple[str, dict[str, Any]]]:
        tree = HTMLParser(html)
        listing = self.recipe.listing
        found: list[tuple[str, dict[str, Any]]] = []

        if listing.item_selector:
            for item in tree.css(listing.item_selector):
                fields = {
                    name: extract_field(item, spec, page_url)
                    for name, spec in listing.fields.items()
                }
                url = fields.get("detail_url")
                if not url:
                    link = item.css_first("a")
                    url = urljoin(page_url, link.attributes.get("href", "")) if link else None
                if url:
                    found.append((str(url), fields))
        elif listing.link_selector:
            for link in tree.css(listing.link_selector):
                href = link.attributes.get("href")
                if href:
                    found.append(
                        (urljoin(page_url, href), {"title": clean_text(link.text(strip=True))})
                    )
        return found

    async def _build_book(self, url: str, listing_fields: dict[str, Any]) -> RawBook | None:
        detail = self.recipe.detail
        fields: dict[str, Any] = {k: v for k, v in listing_fields.items() if v}

        if detail.enabled:
            html = await self._get_text(url, self.recipe.politeness.obey_robots)
            if html is None:
                # A listing row alone is still a usable lead if it named a title.
                return self._assemble(url, fields) if fields.get("title") else None

            tree = HTMLParser(html)
            jsonld = find_book_jsonld(html) if detail.prefer_jsonld else None
            if jsonld:
                for key, value in book_fields_from_jsonld(jsonld).items():
                    if value not in (None, "", []):
                        fields.setdefault(key, value)

            # Selectors run after JSON-LD but take precedence: a recipe author
            # writing an explicit selector is correcting the generic path.
            for name, spec in detail.fields.items():
                value = extract_field(tree, spec, url, jsonld)
                if value not in (None, "", []):
                    fields[name] = value

        return self._assemble(url, fields)

    def _assemble(self, url: str, fields: dict[str, Any]) -> RawBook | None:
        merged = {**self.recipe.constants, **fields}
        title = clean_text(str(merged.get("title") or "")) or None
        if not title:
            return None

        authors = self._authors(merged)
        isbn_raw = merged.get("isbn") or merged.get("isbn13") or merged.get("isbn10")
        isbn = normalize_isbn(str(isbn_raw)) if isbn_raw else None

        return RawBook(
            source_id=self.recipe.id,
            external_id=str(merged.get("external_id") or url),
            url=str(merged.get("url") or url),
            title=title,
            subtitle=clean_text(_first_str(merged.get("subtitle"))),
            authors=authors,
            isbn13=isbn if isbn and len(isbn) == 13 else None,
            isbn10=isbn if isbn and len(isbn) == 10 else None,
            published_date=_first_str(merged.get("published_date")),
            publisher=clean_text(_first_str(merged.get("publisher"))),
            language=_first_str(merged.get("language")),
            page_count=_coerce_int(merged.get("page_count")),
            description=clean_text(_first_str(merged.get("description"))),
            cover_url=_first_str(merged.get("cover_url")),
            categories=_as_str_list(merged.get("categories")),
            ratings_count=_coerce_int(merged.get("ratings_count")),
            average_rating=_coerce_float(merged.get("average_rating")),
            price=_first_str(merged.get("price")),
            raw={"fields": {k: v for k, v in merged.items() if k != "raw"}, "detail_url": url},
        )

    def _authors(self, merged: dict[str, Any]) -> list[RawAuthor]:
        value = merged.get("authors") or merged.get("author")
        entries: list[Any] = value if isinstance(value, list) else ([value] if value else [])
        authors: list[RawAuthor] = []
        for entry in entries:
            if isinstance(entry, dict):
                name = clean_text(entry.get("name"))
                if name:
                    authors.append(
                        RawAuthor(name=name, url=entry.get("url"), bio=entry.get("bio"))
                    )
            elif isinstance(entry, str):
                for name in _split_author_string(entry):
                    authors.append(RawAuthor(name=name))
        return authors


# Sites write multi-author credits as free text; these are the separators that
# appear in practice. Note the character class rather than \b&\b: an ampersand is
# not a word character, so word boundaries around it never match.
_AUTHOR_SPLIT = re.compile(r"\s*(?:[,;&|]|\band\b|\bwith\b)\s*", re.IGNORECASE)
_STRONG_SPLIT = re.compile(r"[;&|]|\band\b|\bwith\b", re.IGNORECASE)
_BYLINE_PREFIX = re.compile(r"^\s*(?:by|written by|author[:s]?)\s*[:\-]?\s*", re.IGNORECASE)


def _split_author_string(value: str) -> list[str]:
    """Split a byline into individual names.

    The hard case is a lone comma, which means either "Family, Given" or two
    people. Bibliographic feeds use the reversed form and arrive through the API
    adapters; HTML book pages overwhelmingly print comma-separated lists. So the
    text is first cut on unambiguous separators, and the reversed reading is then
    taken only for a short chunk holding a single comma.
    """
    text = _BYLINE_PREFIX.sub("", value).strip()
    if not text:
        return []
    names: list[str] = []
    for chunk in _STRONG_SPLIT.split(text):
        names.extend(_resolve_chunk(chunk))
    return names


def _resolve_chunk(chunk: str) -> list[str]:
    text = chunk.strip(" .,-|")
    if not text:
        return []
    if text.count(",") == 1 and len(text.replace(",", " ").split()) <= 3:
        return [text]  # "King, Stephen" and "Doe, Jane M."
    parts = [p.strip(" .,-") for p in text.split(",")]
    return [p for p in parts if len(p) > 1]


def _first_str(value: Any) -> str | None:
    if isinstance(value, list):
        value = value[0] if value else None
    if value is None:
        return None
    return str(value)


def _as_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value if v]


def _coerce_int(value: Any) -> int | None:
    try:
        return int(str(_first_str(value)).strip())
    except (TypeError, ValueError):
        return None


def _coerce_float(value: Any) -> float | None:
    try:
        return float(str(_first_str(value)).strip())
    except (TypeError, ValueError):
        return None
