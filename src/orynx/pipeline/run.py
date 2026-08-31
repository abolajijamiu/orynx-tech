"""Pipeline orchestration: fetch -> store raw -> normalise -> resolve -> score."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from orynx.db.models import (
    RUN_FAILED,
    RUN_PARTIAL,
    RUN_RUNNING,
    RUN_SUCCESS,
    Author,
    AuthorContact,
    Book,
    BookAuthor,
    BookSource,
    CrawlRun,
    Lead,
    RawRecord,
    Source,
)
from orynx.enrich.runner import EnrichmentStats, enrich_authors
from orynx.fetch import PoliteClient
from orynx.logging import get_logger
from orynx.pipeline.dedupe import (
    link_author,
    record_contact,
    record_provenance,
    resolve_author,
    resolve_book,
)
from orynx.pipeline.normalize import normalize_book
from orynx.pipeline.score import DEFAULT_PROFILE, PROFILES, score_lead
from orynx.sources.base import BaseSource, RawBook
from orynx.sources.registry import SourceRegistry, get_registry

log = get_logger(__name__)


@dataclass
class RunStats:
    source_id: str
    fetched: int = 0
    stored: int = 0
    duplicates: int = 0
    books: int = 0
    authors: int = 0
    contacts: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "fetched": self.fetched,
            "stored": self.stored,
            "duplicates": self.duplicates,
            "books": self.books,
            "authors": self.authors,
            "contacts": self.contacts,
            "errors": self.errors[:20],
        }


def ensure_source(session: Session, source: BaseSource) -> Source:
    row = session.get(Source, source.meta.id)
    if row is None:
        row = Source(
            id=source.meta.id,
            name=source.meta.name,
            kind=source.meta.kind,
            homepage=source.meta.homepage,
            trust=source.meta.trust,
        )
        session.add(row)
        session.flush()
    else:
        row.name = source.meta.name
        row.kind = source.meta.kind
        row.trust = source.meta.trust
    return row


def _iterate(source: BaseSource, query: str | None, limit: int) -> AsyncIterator[RawBook]:
    """Pick the right access mode for a source, preferring search when queried."""
    if query and source.supports_search:
        return source.search(query, limit=limit)
    if source.supports_crawl:
        return source.crawl(limit=limit)
    if source.supports_search:
        # A catalogue-less source still answers a wildcard-ish query.
        return source.search(query or "*", limit=limit)
    raise RuntimeError(f"source {source.id} supports neither search nor crawl")


async def ingest_source(
    session: Session,
    client: PoliteClient,
    source: BaseSource,
    *,
    query: str | None = None,
    limit: int = 200,
) -> RunStats:
    """Run one source end to end and persist everything it produced."""
    stats = RunStats(source_id=source.id)
    source_row = ensure_source(session, source)
    run = CrawlRun(
        source_id=source_row.id,
        status=RUN_RUNNING,
        params={"query": query, "limit": limit},
    )
    session.add(run)
    session.flush()

    try:
        async for raw in _iterate(source, query, limit):
            stats.fetched += 1
            try:
                _persist(session, run, source_row, raw, stats)
            except Exception as exc:  # one bad record must not end the crawl
                log.warning("%s: failed to persist a record (%s)", source.id, exc)
                stats.errors.append(str(exc)[:300])
                session.rollback()
                continue

            if stats.fetched % 25 == 0:
                session.commit()

        run.status = RUN_PARTIAL if stats.errors else RUN_SUCCESS
    except Exception as exc:
        log.exception("%s: crawl failed", source.id)
        run.status = RUN_FAILED
        run.error = str(exc)[:2000]
        stats.errors.append(str(exc)[:300])

    run.finished_at = datetime.now(UTC)
    run.stats = stats.as_dict()
    session.commit()
    return stats


def _persist(
    session: Session, run: CrawlRun, source_row: Source, raw: RawBook, stats: RunStats
) -> Book | None:
    content_hash = raw.content_hash()
    already = session.scalar(
        select(RawRecord).where(
            RawRecord.source_id == source_row.id, RawRecord.content_hash == content_hash
        )
    )
    if already is not None:
        stats.duplicates += 1
        return None

    record = RawRecord(
        source_id=source_row.id,
        run_id=run.id,
        external_id=raw.external_id,
        url=raw.url,
        content_hash=content_hash,
        payload=raw.to_payload(),
    )
    session.add(record)
    session.flush()
    stats.stored += 1

    normalized = normalize_book(raw)
    if normalized is None:
        return None

    book = resolve_book(session, normalized)
    stats.books += 1
    record_provenance(session, book, source_row.id, raw.external_id, raw.url, record.id)

    for candidate in normalized.authors or []:
        author = resolve_author(session, candidate)
        link_author(session, book, author, candidate)
        stats.authors += 1
        if candidate.email:
            record_contact(
                session, author, "email", candidate.email,
                source_id=source_row.id, source_url=candidate.source_url or raw.url,
                confidence=0.7,
            )
            stats.contacts += 1

    session.flush()
    return book


async def enrich_pending_authors(
    session: Session,
    client: PoliteClient,
    *,
    limit: int | None = None,
    only_missing_contacts: bool = True,
) -> EnrichmentStats:
    """Run the enrichment chain over authors we cannot yet reach.

    Enrichment is per author, not per book: an author with six titles is looked
    up once, which is both faster and politer than repeating the lookup per book.
    """
    stmt = select(Author)
    if only_missing_contacts:
        reachable = select(AuthorContact.author_id).distinct()
        stmt = stmt.where(~Author.id.in_(reachable))
    if limit is not None:
        stmt = stmt.limit(limit)

    authors = list(session.scalars(stmt).all())
    if not authors:
        return EnrichmentStats()
    log.info("enriching %s author(s)", len(authors))
    return await enrich_authors(session, client, authors)


def rebuild_leads(session: Session, profile: str = DEFAULT_PROFILE) -> int:
    """Score every author/book pair. Cheap enough to re-run after any ingest."""
    if profile not in PROFILES:
        raise ValueError(f"unknown profile {profile!r}; choose from {sorted(PROFILES)}")

    # Pre-compute the per-author and per-book aggregates the scorer needs, so the
    # scoring loop issues no queries of its own.
    book_counts = dict(
        session.execute(
            select(BookAuthor.author_id, func.count(BookAuthor.book_id)).group_by(
                BookAuthor.author_id
            )
        ).all()
    )
    contact_counts = dict(
        session.execute(
            select(AuthorContact.author_id, func.count(AuthorContact.id)).group_by(
                AuthorContact.author_id
            )
        ).all()
    )
    source_counts = dict(
        session.execute(
            select(BookSource.book_id, func.count(func.distinct(BookSource.source_id))).group_by(
                BookSource.book_id
            )
        ).all()
    )
    trust_by_source = dict(session.execute(select(Source.id, Source.trust)).all())
    best_trust: dict[int, float] = {}
    for book_id, source_id in session.execute(
        select(BookSource.book_id, BookSource.source_id)
    ).all():
        trust = trust_by_source.get(source_id, 0.5)
        best_trust[book_id] = max(best_trust.get(book_id, 0.0), trust)

    existing = {
        (lead.author_id, lead.book_id): lead for lead in session.scalars(select(Lead)).all()
    }

    count = 0
    for link in session.scalars(select(BookAuthor)).all():
        author = session.get(Author, link.author_id)
        book = session.get(Book, link.book_id)
        if author is None or book is None:
            continue
        result = score_lead(
            author,
            book,
            profile=profile,
            source_trust=best_trust.get(book.id, 0.5),
            source_count=source_counts.get(book.id, 1),
            book_count=book_counts.get(author.id, 1),
            contact_count=contact_counts.get(author.id, 0),
        )
        lead = existing.get((author.id, book.id))
        if lead is None:
            lead = Lead(author_id=author.id, book_id=book.id)
            session.add(lead)
        lead.score = result.score
        lead.tier = result.tier
        lead.reasons = result.reasons
        count += 1

    session.commit()
    return count


async def run_pipeline(
    session: Session,
    source_ids: list[str],
    *,
    query: str | None = None,
    limit: int = 200,
    profile: str = DEFAULT_PROFILE,
    with_contacts: bool = False,
    registry: SourceRegistry | None = None,
    client: PoliteClient | None = None,
) -> list[RunStats]:
    """Ingest from every requested source, then rescore the whole lead table."""
    registry = registry or get_registry()
    owns_client = client is None
    client = client or PoliteClient()
    results: list[RunStats] = []
    try:
        for source_id in source_ids:
            try:
                source = registry.build(source_id, client)
            except KeyError as exc:
                log.error("%s", exc)
                results.append(RunStats(source_id=source_id, errors=[str(exc)]))
                continue
            log.info("ingesting %s (limit=%s)", source_id, limit)
            results.append(
                await ingest_source(session, client, source, query=query, limit=limit)
            )
        if with_contacts:
            enrichment = await enrich_pending_authors(session, client)
            log.info(
                "enrichment: %s/%s authors reachable, %s contacts added",
                enrichment.enriched,
                enrichment.attempted,
                enrichment.contacts_added,
            )
    finally:
        if owns_client:
            await client.aclose()

    rebuild_leads(session, profile)
    return results


def run_pipeline_sync(*args, **kwargs) -> list[RunStats]:
    return asyncio.run(run_pipeline(*args, **kwargs))
