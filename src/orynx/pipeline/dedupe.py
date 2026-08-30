"""Entity resolution: collapse the same book or author seen on many platforms.

Matching is two-stage. A cheap exact key (ISBN, or folded title plus author
block) finds most pairs; fuzzy comparison then runs only inside a block, which
keeps the cost linear rather than quadratic in the number of records.
"""

from __future__ import annotations

from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.orm import Session

from orynx.db.models import Author, AuthorContact, Book, BookAuthor, BookSource
from orynx.logging import get_logger
from orynx.pipeline.normalize import NormalizedAuthor, NormalizedBook
from orynx.textutil import normalize_title

log = get_logger(__name__)

# Tuned to accept "Jon Smith" vs "John Smith" while rejecting siblings such as
# "Jane Smith" vs "Joan Smith". Raise it if you see false merges in your data.
AUTHOR_MATCH_THRESHOLD = 92
TITLE_MATCH_THRESHOLD = 94


def resolve_author(session: Session, candidate: NormalizedAuthor) -> Author:
    """Find or create the Author row for a normalised author."""
    exact = session.scalar(
        select(Author).where(Author.normalized_name == candidate.normalized_name).limit(1)
    )
    if exact is not None:
        _enrich_author(exact, candidate)
        return exact

    if candidate.dedupe_key:
        block = session.scalars(
            select(Author).where(Author.dedupe_key == candidate.dedupe_key).limit(50)
        ).all()
        best: Author | None = None
        best_score = 0.0
        for existing in block:
            score = fuzz.WRatio(existing.normalized_name, candidate.normalized_name)
            if score > best_score:
                best, best_score = existing, score
        if best is not None and best_score >= AUTHOR_MATCH_THRESHOLD:
            log.debug(
                "author match %r ~ %r (%.0f)",
                candidate.display_name,
                best.display_name,
                best_score,
            )
            _enrich_author(best, candidate)
            return best

    author = Author(
        display_name=candidate.display_name,
        normalized_name=candidate.normalized_name,
        dedupe_key=candidate.dedupe_key,
        bio=candidate.bio,
        website=candidate.website,
    )
    session.add(author)
    session.flush()
    return author


def _enrich_author(author: Author, candidate: NormalizedAuthor) -> None:
    """Fill gaps on an existing author without overwriting what we already hold."""
    if not author.bio and candidate.bio:
        author.bio = candidate.bio
    if not author.website and candidate.website:
        author.website = candidate.website


def record_contact(
    session: Session,
    author: Author,
    kind: str,
    value: str,
    *,
    source_id: str | None,
    source_url: str | None,
    confidence: float = 0.5,
) -> AuthorContact | None:
    """Store a contact point with its provenance, ignoring duplicates."""
    value = value.strip()
    if not value:
        return None
    existing = session.scalar(
        select(AuthorContact).where(
            AuthorContact.author_id == author.id,
            AuthorContact.kind == kind,
            AuthorContact.value == value,
        )
    )
    if existing is not None:
        return existing
    contact = AuthorContact(
        author_id=author.id,
        kind=kind,
        value=value[:500],
        source_id=source_id,
        source_url=source_url,
        confidence=confidence,
    )
    session.add(contact)
    return contact


def _author_block_of(candidate: NormalizedBook) -> str:
    """The first author's blocking key, however the dedupe key was built."""
    if candidate.authors:
        return candidate.authors[0].dedupe_key
    if "|a:" in candidate.dedupe_key:
        return candidate.dedupe_key.split("|a:")[-1]
    return ""


def resolve_book(session: Session, candidate: NormalizedBook) -> Book:
    """Find or create the Book row, merging metadata from every platform seen."""
    existing: Book | None = None

    if candidate.isbn13:
        existing = session.scalar(select(Book).where(Book.isbn13 == candidate.isbn13).limit(1))

    if existing is None:
        existing = session.scalar(
            select(Book).where(Book.dedupe_key == candidate.dedupe_key).limit(1)
        )

    if existing is None:
        # Same author, near-identical title: catches subtitle and punctuation drift,
        # and — importantly — matches an incoming record that carries an ISBN
        # against one already stored without one.
        prefix = _author_block_of(candidate)
        if prefix:
            block = session.scalars(
                select(Book)
                .join(BookAuthor, BookAuthor.book_id == Book.id)
                .join(Author, Author.id == BookAuthor.author_id)
                .where(Author.dedupe_key == prefix)
                .limit(100)
            ).all()
            target = normalize_title(candidate.title)
            for book in block:
                # Two different ISBNs are two different records; only merge when
                # the stored book has none, or they agree.
                if book.isbn13 and candidate.isbn13 and book.isbn13 != candidate.isbn13:
                    continue
                if fuzz.ratio(normalize_title(book.title), target) >= TITLE_MATCH_THRESHOLD:
                    existing = book
                    break

    if existing is not None:
        _merge_book(existing, candidate)
        return existing

    book = Book(**candidate.as_columns())
    session.add(book)
    session.flush()
    return book


def _merge_book(book: Book, candidate: NormalizedBook) -> None:
    """Prefer existing values; fill only what is missing.

    Ratings are the exception: the highest count seen wins, because a platform
    reporting more ratings has the better view of the book's actual visibility.
    """
    for field in (
        "subtitle", "isbn13", "isbn10", "published_on", "published_year",
        "publisher", "language", "page_count", "description", "cover_url",
    ):
        if getattr(book, field, None) in (None, "") and getattr(candidate, field, None):
            setattr(book, field, getattr(candidate, field))

    if candidate.categories:
        merged = list(dict.fromkeys(list(book.categories or []) + candidate.categories))
        book.categories = merged[:30]

    if candidate.ratings_count is not None:
        if book.ratings_count is None or candidate.ratings_count > book.ratings_count:
            book.ratings_count = candidate.ratings_count
            if candidate.average_rating is not None:
                book.average_rating = candidate.average_rating

    if book.isbn13 and book.dedupe_key.startswith("t:"):
        # Gaining an ISBN upgrades the book to the authoritative identity.
        book.dedupe_key = f"isbn:{book.isbn13}"


def link_author(session: Session, book: Book, author: Author, candidate: NormalizedAuthor) -> None:
    existing = session.scalar(
        select(BookAuthor).where(
            BookAuthor.book_id == book.id, BookAuthor.author_id == author.id
        )
    )
    if existing is not None:
        return
    session.add(
        BookAuthor(
            book_id=book.id,
            author_id=author.id,
            role=candidate.role,
            position=candidate.position,
        )
    )


def record_provenance(
    session: Session,
    book: Book,
    source_id: str,
    external_id: str | None,
    url: str | None,
    raw_record_id: int | None = None,
) -> None:
    existing = session.scalar(
        select(BookSource).where(
            BookSource.book_id == book.id,
            BookSource.source_id == source_id,
            BookSource.external_id == external_id,
        )
    )
    if existing is not None:
        existing.url = url or existing.url
        return
    session.add(
        BookSource(
            book_id=book.id,
            source_id=source_id,
            external_id=external_id,
            url=url,
            raw_record_id=raw_record_id,
        )
    )
