"""Normalisation, entity resolution and the end-to-end pipeline."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date

import pytest
from sqlalchemy import func, select

from orynx.db.models import Author, Book, BookAuthor, BookSource, Lead, RawRecord
from orynx.pipeline.dedupe import link_author, resolve_author, resolve_book
from orynx.pipeline.normalize import book_dedupe_key, normalize_book
from orynx.pipeline.run import ingest_source, rebuild_leads
from orynx.pipeline.score import PROFILES, score_lead
from orynx.sources.base import BaseSource, RawAuthor, RawBook, SourceMeta


def raw(
    title="The Quiet Harbour", authors=("Amara Nwosu",), isbn13=None, source_id="test", **kwargs
):
    return RawBook(
        source_id=source_id,
        title=title,
        authors=[RawAuthor(name=a) for a in authors],
        isbn13=isbn13,
        **kwargs,
    )


# --------------------------------------------------------------------------- #
# Normalisation
# --------------------------------------------------------------------------- #

def test_normalize_derives_isbn13_from_isbn10():
    book = normalize_book(raw(isbn10="0306406152"))
    assert book.isbn13 == "9780306406157"


def test_normalize_recovers_from_isbn10_in_the_isbn13_field():
    book = normalize_book(raw(isbn13="0306406152"))
    assert book.isbn10 == "0306406152"
    assert book.isbn13 == "9780306406157"


def test_normalize_rejects_untitled_records():
    assert normalize_book(raw(title="   ")) is None


def test_normalize_drops_impossible_publication_dates():
    assert normalize_book(raw(published_date="1200")).published_year is None
    assert normalize_book(raw(published_date="2099")).published_year is None


def test_normalize_deduplicates_repeated_authors():
    book = normalize_book(raw(authors=("Amara Nwosu", "amara  nwosu", "Peter Blake")))
    assert [a.display_name for a in book.authors] == ["Amara Nwosu", "Peter Blake"]


def test_dedupe_key_prefers_isbn():
    assert book_dedupe_key("9780306406157", "Any Title", "Any Author") == "isbn:9780306406157"


def test_dedupe_key_without_isbn_uses_title_and_author():
    a = book_dedupe_key(None, "The Quiet Harbour", "Amara Nwosu")
    b = book_dedupe_key(None, "Quiet Harbour", "Nwosu, Amara")
    assert a == b  # same book, different formatting on two platforms


def test_dedupe_key_separates_same_title_by_different_authors():
    a = book_dedupe_key(None, "Home", "Amara Nwosu")
    b = book_dedupe_key(None, "Home", "Peter Blake")
    assert a != b


# --------------------------------------------------------------------------- #
# Entity resolution
# --------------------------------------------------------------------------- #

def test_same_isbn_from_two_platforms_collapses_to_one_book(session):
    first = normalize_book(raw(isbn13="9781234567897", source_id="openlibrary"))
    second = normalize_book(
        raw(title="Quiet Harbour, The", isbn13="9781234567897", source_id="googlebooks")
    )
    resolve_book(session, first)
    resolve_book(session, second)
    session.commit()
    assert session.scalar(select(func.count()).select_from(Book)) == 1


def test_books_without_isbn_merge_on_title_and_author(session):
    resolve_book(session, normalize_book(raw()))
    resolve_book(session, normalize_book(raw(title="The Quiet Harbour")))
    session.commit()
    assert session.scalar(select(func.count()).select_from(Book)) == 1


def test_author_name_orderings_resolve_to_one_author(session):
    a = normalize_book(raw(authors=("Amara Nwosu",))).authors[0]
    b = normalize_book(raw(authors=("Nwosu, Amara",))).authors[0]
    first = resolve_author(session, a)
    second = resolve_author(session, b)
    session.commit()
    assert first.id == second.id
    assert session.scalar(select(func.count()).select_from(Author)) == 1


def test_distinct_authors_are_not_merged(session):
    a = normalize_book(raw(authors=("Amara Nwosu",))).authors[0]
    b = normalize_book(raw(authors=("Peter Blake",))).authors[0]
    assert resolve_author(session, a).id != resolve_author(session, b).id


def test_similar_names_in_one_block_are_not_over_merged(session):
    a = normalize_book(raw(authors=("Jane Smith",))).authors[0]
    b = normalize_book(raw(authors=("Joan Smith",))).authors[0]
    session.commit()
    assert resolve_author(session, a).id != resolve_author(session, b).id


def test_merge_fills_gaps_without_overwriting(session):
    sparse = normalize_book(raw(isbn13="9781234567897"))
    book = resolve_book(session, sparse)
    session.commit()
    assert book.publisher is None

    richer = normalize_book(
        raw(isbn13="9781234567897", publisher="Koehler Books", page_count=312)
    )
    resolve_book(session, richer)
    session.commit()
    assert book.publisher == "Koehler Books"
    assert book.page_count == 312


def test_merge_keeps_the_highest_ratings_count(session):
    book = resolve_book(session, normalize_book(raw(isbn13="9781234567897", ratings_count=4)))
    resolve_book(session, normalize_book(raw(isbn13="9781234567897", ratings_count=980)))
    session.commit()
    assert book.ratings_count == 980


def test_gaining_an_isbn_upgrades_the_dedupe_key(session):
    normalized = normalize_book(raw())
    book = resolve_book(session, normalized)
    author = resolve_author(session, normalized.authors[0])
    link_author(session, book, author, normalized.authors[0])
    session.commit()
    assert book.dedupe_key.startswith("t:")

    resolve_book(session, normalize_book(raw(isbn13="9781234567897")))
    session.commit()
    assert book.dedupe_key == "isbn:9781234567897"


def test_link_author_is_idempotent(session):
    normalized = normalize_book(raw())
    book = resolve_book(session, normalized)
    author = resolve_author(session, normalized.authors[0])
    link_author(session, book, author, normalized.authors[0])
    link_author(session, book, author, normalized.authors[0])
    session.commit()
    assert session.scalar(select(func.count()).select_from(BookAuthor)) == 1


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #

def _author():
    return Author(
        display_name="Amara Nwosu", normalized_name="amara nwosu",
        dedupe_key="nwosu:a", website="https://amara.example",
    )


def test_recent_self_published_debut_scores_high_for_services():
    book = Book(
        title="Debut", publisher="Independently published",
        published_on=date(2026, 6, 1), published_year=2026, ratings_count=2, dedupe_key="k",
    )
    result = score_lead(_author(), book, profile="services", today=date(2026, 8, 30))
    assert result.score > 70
    assert result.tier == "A"


def test_old_trade_bestseller_scores_low_for_services():
    book = Book(
        title="Backlist", publisher="Penguin Random House",
        published_on=date(2015, 1, 1), published_year=2015, ratings_count=50000, dedupe_key="k",
    )
    assert score_lead(_author(), book, profile="services", today=date(2026, 8, 30)).score < 30


def test_rights_profile_inverts_the_services_ranking():
    trade = Book(
        title="Backlist", publisher="Penguin Random House",
        published_on=date(2024, 1, 1), published_year=2024, ratings_count=50000, dedupe_key="k",
    )
    services = score_lead(_author(), trade, profile="services", today=date(2026, 8, 30))
    rights = score_lead(_author(), trade, profile="rights", today=date(2026, 8, 30))
    assert rights.score > services.score


def test_forthcoming_titles_get_full_recency():
    book = Book(title="Soon", published_on=date(2026, 12, 1), published_year=2026, dedupe_key="k")
    result = score_lead(_author(), book, profile="marketing", today=date(2026, 8, 30))
    recency = next(r for r in result.reasons if r["signal"] == "recency")
    assert recency["value"] == 1.0


def test_missing_publisher_is_treated_as_author_funded():
    book = Book(title="Unknown", published_year=2026, dedupe_key="k")
    result = score_lead(_author(), book, profile="services", today=date(2026, 8, 30))
    funded = next(r for r in result.reasons if r["signal"] == "author_funded")
    assert funded["value"] == 1.0


def test_reasons_are_sorted_by_contribution():
    book = Book(title="X", publisher="Lulu", published_year=2026, dedupe_key="k")
    result = score_lead(_author(), book, profile="services", today=date(2026, 8, 30))
    points = [r["points"] for r in result.reasons]
    assert points == sorted(points, reverse=True)


def test_every_profile_produces_a_score_in_range():
    book = Book(title="X", publisher="Lulu", published_year=2025, ratings_count=10, dedupe_key="k")
    for name in PROFILES:
        result = score_lead(_author(), book, profile=name, today=date(2026, 8, 30))
        assert 0.0 <= result.score <= 100.0
        assert result.tier in {"A", "B", "C", "D"}


# --------------------------------------------------------------------------- #
# End to end
# --------------------------------------------------------------------------- #

class FakeSource(BaseSource):
    meta = SourceMeta(id="fake", name="Fake", kind="hybrid", trust=0.7)

    def __init__(self, client=None, books=None):
        self.client = client
        self._books = books or []

    async def crawl(self, *, limit=None) -> AsyncIterator[RawBook]:
        for book in self._books[: limit or len(self._books)]:
            yield book


@pytest.mark.asyncio
async def test_ingest_persists_raw_books_authors_and_provenance(session):
    books = [
        raw(isbn13="9781234567897", source_id="fake", publisher="Koehler Books",
            published_date="2026-03-15", url="https://press.test/a"),
        raw(title="Tidewater", authors=("Amara Nwosu", "Peter Blake"),
            isbn13="9780306406157", source_id="fake", url="https://press.test/b"),
    ]
    stats = await ingest_source(session, None, FakeSource(books=books), limit=10)

    assert stats.fetched == 2
    assert stats.stored == 2
    assert session.scalar(select(func.count()).select_from(Book)) == 2
    # Amara appears on both books but resolves to a single author row.
    assert session.scalar(select(func.count()).select_from(Author)) == 2
    assert session.scalar(select(func.count()).select_from(BookSource)) == 2


@pytest.mark.asyncio
async def test_reingesting_identical_records_stores_no_duplicates(session):
    books = [raw(isbn13="9781234567897", source_id="fake")]
    await ingest_source(session, None, FakeSource(books=books), limit=10)
    stats = await ingest_source(session, None, FakeSource(books=books), limit=10)

    assert stats.duplicates == 1
    assert stats.stored == 0
    assert session.scalar(select(func.count()).select_from(RawRecord)) == 1
    assert session.scalar(select(func.count()).select_from(Book)) == 1


@pytest.mark.asyncio
async def test_same_book_from_two_platforms_yields_one_book_two_provenances(session):
    await ingest_source(
        session, None,
        FakeSource(books=[raw(isbn13="9781234567897", source_id="fake")]), limit=5,
    )

    class OtherSource(FakeSource):
        meta = SourceMeta(id="other", name="Other", kind="retailer", trust=0.6)

    await ingest_source(
        session, None,
        OtherSource(books=[raw(title="Quiet Harbour", isbn13="9781234567897",
                               source_id="other")]),
        limit=5,
    )

    assert session.scalar(select(func.count()).select_from(Book)) == 1
    assert session.scalar(select(func.count()).select_from(BookSource)) == 2


@pytest.mark.asyncio
async def test_rebuild_leads_scores_every_author_book_pair(session):
    books = [
        raw(isbn13="9781234567897", source_id="fake", publisher="Koehler Books",
            published_date="2026-03-15"),
        raw(title="Tidewater", authors=("Amara Nwosu", "Peter Blake"),
            isbn13="9780306406157", source_id="fake"),
    ]
    await ingest_source(session, None, FakeSource(books=books), limit=10)
    count = rebuild_leads(session, "services")

    assert count == 3  # Amara on two books, Peter on one
    leads = session.scalars(select(Lead)).all()
    assert all(lead.score > 0 for lead in leads)
    assert all(lead.reasons for lead in leads)


@pytest.mark.asyncio
async def test_rebuild_leads_rejects_an_unknown_profile(session):
    with pytest.raises(ValueError, match="unknown profile"):
        rebuild_leads(session, "nonsense")


@pytest.mark.asyncio
async def test_a_bad_record_does_not_abort_the_crawl(session):
    books = [raw(isbn13="9781234567897"), raw(title=""), raw(title="Third")]
    stats = await ingest_source(session, None, FakeSource(books=books), limit=10)
    assert stats.fetched == 3
    assert session.scalar(select(func.count()).select_from(Book)) == 2


def test_isbn_bearing_record_merges_into_an_isbn_less_book(session):
    """A richer source arriving second must not create a duplicate book."""
    normalized = normalize_book(raw())
    book = resolve_book(session, normalized)
    author = resolve_author(session, normalized.authors[0])
    link_author(session, book, author, normalized.authors[0])
    session.commit()

    resolve_book(session, normalize_book(raw(isbn13="9781234567897")))
    session.commit()

    assert session.scalar(select(func.count()).select_from(Book)) == 1
    assert book.isbn13 == "9781234567897"


def test_distinct_isbns_with_the_same_title_stay_separate(session):
    first = normalize_book(raw(isbn13="9781234567897"))
    book = resolve_book(session, first)
    author = resolve_author(session, first.authors[0])
    link_author(session, book, author, first.authors[0])
    session.commit()

    resolve_book(session, normalize_book(raw(isbn13="9780306406157")))
    session.commit()
    assert session.scalar(select(func.count()).select_from(Book)) == 2
