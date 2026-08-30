"""Flatten scored leads into export rows.

Suppression is applied here, at the single point every export format passes
through, so no output path can bypass an opt-out.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from orynx.compliance.suppression import SuppressionList
from orynx.db.models import Author, Book, BookSource, Lead

COLUMNS = [
    "lead_id", "score", "tier", "status",
    "author_name", "author_website", "author_emails", "author_socials",
    "book_title", "book_subtitle", "isbn13", "published_on", "published_year",
    "publisher", "language", "page_count", "ratings_count", "average_rating",
    "categories", "sources", "source_urls", "top_signals", "notes",
]


@dataclass
class LeadRow:
    lead_id: int
    score: float
    tier: str
    status: str
    author_name: str
    author_website: str = ""
    author_emails: str = ""
    author_socials: str = ""
    book_title: str = ""
    book_subtitle: str = ""
    isbn13: str = ""
    published_on: str = ""
    published_year: str = ""
    publisher: str = ""
    language: str = ""
    page_count: str = ""
    ratings_count: str = ""
    average_rating: str = ""
    categories: str = ""
    sources: str = ""
    source_urls: str = ""
    top_signals: str = ""
    notes: str = ""
    _extra: dict[str, Any] = field(default_factory=dict, repr=False)

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("_extra", None)
        return data


def build_rows(
    session: Session,
    *,
    min_score: float = 0.0,
    tiers: list[str] | None = None,
    limit: int | None = None,
    require_contact: bool = False,
    apply_suppression: bool = True,
) -> tuple[list[LeadRow], int]:
    """Return (rows, suppressed_count) for the leads matching the filters."""
    suppression = SuppressionList.load(session) if apply_suppression else None

    stmt = (
        select(Lead)
        .options(
            selectinload(Lead.author).selectinload(Author.contacts),
            selectinload(Lead.book).selectinload(Book.provenance),
        )
        .where(Lead.score >= min_score)
        .order_by(Lead.score.desc())
    )
    if tiers:
        stmt = stmt.where(Lead.tier.in_([t.upper() for t in tiers]))

    rows: list[LeadRow] = []
    suppressed = 0
    for lead in session.scalars(stmt).all():
        author, book = lead.author, lead.book
        if author is None or book is None:
            continue

        emails = [c.value for c in author.contacts if c.kind == "email"]
        socials = [f"{c.kind}={c.value}" for c in author.contacts if c.kind != "email"]

        if require_contact and not emails and not author.website and not socials:
            continue

        if suppression is not None:
            reason = suppression.blocks(author_name=author.display_name, emails=emails)
            if reason:
                suppressed += 1
                continue

        provenance: list[BookSource] = list(book.provenance or [])
        rows.append(
            LeadRow(
                lead_id=lead.id,
                score=lead.score,
                tier=lead.tier,
                status=lead.status,
                author_name=author.display_name,
                author_website=author.website or "",
                author_emails="; ".join(emails),
                author_socials="; ".join(socials),
                book_title=book.title,
                book_subtitle=book.subtitle or "",
                isbn13=book.isbn13 or "",
                published_on=book.published_on.isoformat() if book.published_on else "",
                published_year=str(book.published_year or ""),
                publisher=book.publisher or "",
                language=book.language or "",
                page_count=str(book.page_count or ""),
                ratings_count=str(book.ratings_count if book.ratings_count is not None else ""),
                average_rating=str(
                    book.average_rating if book.average_rating is not None else ""
                ),
                categories="; ".join(book.categories or []),
                sources="; ".join(sorted({p.source_id for p in provenance})),
                source_urls=" | ".join(p.url for p in provenance if p.url),
                top_signals="; ".join(
                    f"{r.get('signal')}({r.get('points')})" for r in (lead.reasons or [])[:3]
                ),
                notes=lead.notes or "",
            )
        )
        if limit is not None and len(rows) >= limit:
            break

    return rows, suppressed
