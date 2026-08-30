"""RawBook -> the field shapes the database expects.

Normalisation is deliberately separate from persistence so it can be tested
without a database, and so a bad parse is visible before it reaches storage.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from orynx.sources.base import RawAuthor, RawBook
from orynx.textutil import (
    clean_text,
    isbn10_to_13,
    normalize_isbn,
    normalize_person,
    normalize_title,
    parse_date,
    person_block_key,
)

MAX_TITLE = 500
MAX_DESCRIPTION = 20000


@dataclass(slots=True)
class NormalizedAuthor:
    display_name: str
    normalized_name: str
    dedupe_key: str
    role: str = "author"
    position: int = 0
    bio: str | None = None
    website: str | None = None
    email: str | None = None
    source_url: str | None = None


@dataclass(slots=True)
class NormalizedBook:
    title: str
    dedupe_key: str
    subtitle: str | None = None
    isbn13: str | None = None
    isbn10: str | None = None
    published_on: date | None = None
    published_year: int | None = None
    publisher: str | None = None
    language: str | None = None
    page_count: int | None = None
    description: str | None = None
    cover_url: str | None = None
    categories: list[str] | None = None
    ratings_count: int | None = None
    average_rating: float | None = None
    authors: list[NormalizedAuthor] | None = None
    source_id: str = ""
    external_id: str | None = None
    url: str | None = None

    def as_columns(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "subtitle": self.subtitle,
            "isbn13": self.isbn13,
            "isbn10": self.isbn10,
            "published_on": self.published_on,
            "published_year": self.published_year,
            "publisher": self.publisher,
            "language": self.language,
            "page_count": self.page_count,
            "description": self.description,
            "cover_url": self.cover_url,
            "categories": self.categories or [],
            "ratings_count": self.ratings_count,
            "average_rating": self.average_rating,
            "dedupe_key": self.dedupe_key,
        }


def normalize_author(raw: RawAuthor, position: int = 0) -> NormalizedAuthor | None:
    name = clean_text(raw.name)
    if not name or len(name) < 2:
        return None
    normalized = normalize_person(name)
    if not normalized:
        return None
    return NormalizedAuthor(
        display_name=name[:300],
        normalized_name=normalized[:300],
        dedupe_key=person_block_key(name)[:120],
        role=raw.role or "author",
        position=position,
        bio=clean_text(raw.bio),
        website=raw.website or raw.url,
        email=(raw.email or "").lower() or None,
        source_url=raw.url,
    )


def book_dedupe_key(isbn13: str | None, title: str, first_author: str | None) -> str:
    """A stable identity for a book across platforms.

    An ISBN is authoritative when present. Otherwise the key pairs the folded
    title with the first author's blocking key, which keeps different books
    sharing a common title apart.
    """
    if isbn13:
        return f"isbn:{isbn13}"
    title_key = normalize_title(title)[:80]
    author_key = person_block_key(first_author) if first_author else ""
    return f"t:{title_key}|a:{author_key}"[:120]


def normalize_book(raw: RawBook) -> NormalizedBook | None:
    title = clean_text(raw.title)
    if not title:
        return None

    isbn13 = normalize_isbn(raw.isbn13)
    isbn10 = normalize_isbn(raw.isbn10)
    if isbn13 and len(isbn13) == 10:  # a 10 was supplied in the 13 field
        isbn10, isbn13 = isbn13, None
    if not isbn13 and isbn10:
        isbn13 = isbn10_to_13(isbn10)

    authors: list[NormalizedAuthor] = []
    for index, raw_author in enumerate(raw.authors or []):
        normalized = normalize_author(raw_author, index)
        if normalized and not any(a.normalized_name == normalized.normalized_name for a in authors):
            authors.append(normalized)

    published_on, published_year = parse_date(raw.published_date)
    # Guard against parse noise: a book dated beyond next year is bad data, and a
    # pre-1450 date predates printing.
    if published_year and not (1450 <= published_year <= date.today().year + 2):
        published_on, published_year = None, None

    return NormalizedBook(
        title=title[:MAX_TITLE],
        subtitle=(clean_text(raw.subtitle) or "")[:MAX_TITLE] or None,
        isbn13=isbn13,
        isbn10=isbn10,
        published_on=published_on,
        published_year=published_year,
        publisher=(clean_text(raw.publisher) or "")[:300] or None,
        language=(raw.language or "")[:16] or None,
        page_count=raw.page_count if isinstance(raw.page_count, int) else None,
        description=(clean_text(raw.description) or "")[:MAX_DESCRIPTION] or None,
        cover_url=(raw.cover_url or "")[:1000] or None,
        categories=[c for c in (raw.categories or []) if c][:20],
        ratings_count=raw.ratings_count,
        average_rating=raw.average_rating,
        authors=authors,
        dedupe_key=book_dedupe_key(isbn13, title, authors[0].display_name if authors else None),
        source_id=raw.source_id,
        external_id=raw.external_id,
        url=raw.url,
    )
