"""Schema for the lead pipeline.

The shape follows the pipeline stages: `RawRecord` keeps exactly what a platform
returned so a crawl can be replayed without re-fetching, `Book`/`Author` hold the
deduplicated entities, and `Lead` is the scored, exportable join of the two.
Every derived row can be traced back to a URL, which is what makes a deletion
request answerable.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from orynx.db.base import Base

# Source kinds drive scoring: a hybrid publisher's author is a warmer lead for
# author services than a trade-published one, who already has a team.
SOURCE_KIND_BIBLIOGRAPHIC = "bibliographic"
SOURCE_KIND_PUBLISHER = "publisher"
SOURCE_KIND_HYBRID = "hybrid"
SOURCE_KIND_RETAILER = "retailer"
SOURCE_KIND_REVIEW = "review"
SOURCE_KIND_CROWDFUNDING = "crowdfunding"
SOURCE_KIND_DIRECTORY = "directory"

RUN_PENDING = "pending"
RUN_RUNNING = "running"
RUN_SUCCESS = "success"
RUN_FAILED = "failed"
RUN_PARTIAL = "partial"

LEAD_NEW = "new"
LEAD_QUALIFIED = "qualified"
LEAD_CONTACTED = "contacted"
LEAD_REJECTED = "rejected"
LEAD_SUPPRESSED = "suppressed"


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Source(Base, TimestampMixin):
    """A platform we extract from. Rows mirror API adapters and YAML recipes."""

    __tablename__ = "source"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    homepage: Mapped[str | None] = mapped_column(String(500))
    # 0..1 confidence in the data quality of this platform; feeds lead scoring.
    trust: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    config: Mapped[dict] = mapped_column(JSON, default=dict)

    runs: Mapped[list[CrawlRun]] = relationship(back_populates="source")


class CrawlRun(Base):
    __tablename__ = "crawl_run"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("source.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(16), default=RUN_PENDING, nullable=False)
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    stats: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    source: Mapped[Source] = relationship(back_populates="runs")


class RawRecord(Base):
    """Untouched platform payload.

    `content_hash` makes re-crawls idempotent: an unchanged listing is recognised
    without re-parsing, and a changed one is stored as a new revision.
    """

    __tablename__ = "raw_record"
    __table_args__ = (
        UniqueConstraint("source_id", "content_hash", name="uq_raw_source_hash"),
        Index("ix_raw_source_external", "source_id", "external_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("source.id", ondelete="CASCADE"))
    run_id: Mapped[int | None] = mapped_column(ForeignKey("crawl_run.id", ondelete="SET NULL"))
    external_id: Mapped[str | None] = mapped_column(String(255))
    url: Mapped[str | None] = mapped_column(String(1000))
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Book(Base, TimestampMixin):
    __tablename__ = "book"
    __table_args__ = (
        Index("ix_book_dedupe", "dedupe_key"),
        Index("ix_book_isbn13", "isbn13"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    subtitle: Mapped[str | None] = mapped_column(String(500))
    isbn13: Mapped[str | None] = mapped_column(String(13))
    isbn10: Mapped[str | None] = mapped_column(String(10))
    published_on: Mapped[date | None] = mapped_column(Date)
    published_year: Mapped[int | None] = mapped_column(Integer)
    publisher: Mapped[str | None] = mapped_column(String(300))
    language: Mapped[str | None] = mapped_column(String(16))
    page_count: Mapped[int | None] = mapped_column(Integer)
    description: Mapped[str | None] = mapped_column(Text)
    cover_url: Mapped[str | None] = mapped_column(String(1000))
    categories: Mapped[list] = mapped_column(JSON, default=list)
    # Ratings are a visibility signal: a new book with no reviews is a marketing lead.
    ratings_count: Mapped[int | None] = mapped_column(Integer)
    average_rating: Mapped[float | None] = mapped_column(Float)
    dedupe_key: Mapped[str] = mapped_column(String(120), nullable=False)

    authors: Mapped[list[BookAuthor]] = relationship(
        back_populates="book", cascade="all, delete-orphan"
    )
    provenance: Mapped[list[BookSource]] = relationship(
        back_populates="book", cascade="all, delete-orphan"
    )


class BookSource(Base):
    """Which platforms reported a given book, and where."""

    __tablename__ = "book_source"
    __table_args__ = (
        UniqueConstraint("book_id", "source_id", "external_id", name="uq_book_source"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("book.id", ondelete="CASCADE"))
    source_id: Mapped[str] = mapped_column(ForeignKey("source.id", ondelete="CASCADE"))
    external_id: Mapped[str | None] = mapped_column(String(255))
    url: Mapped[str | None] = mapped_column(String(1000))
    raw_record_id: Mapped[int | None] = mapped_column(
        ForeignKey("raw_record.id", ondelete="SET NULL")
    )
    first_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    book: Mapped[Book] = relationship(back_populates="provenance")


class Author(Base, TimestampMixin):
    __tablename__ = "author"
    __table_args__ = (Index("ix_author_dedupe", "dedupe_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    display_name: Mapped[str] = mapped_column(String(300), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(300), nullable=False)
    sort_name: Mapped[str | None] = mapped_column(String(300))
    dedupe_key: Mapped[str] = mapped_column(String(120), nullable=False)
    bio: Mapped[str | None] = mapped_column(Text)
    website: Mapped[str | None] = mapped_column(String(500))
    country: Mapped[str | None] = mapped_column(String(80))

    books: Mapped[list[BookAuthor]] = relationship(
        back_populates="author", cascade="all, delete-orphan"
    )
    contacts: Mapped[list[AuthorContact]] = relationship(
        back_populates="author", cascade="all, delete-orphan"
    )


class BookAuthor(Base):
    __tablename__ = "book_author"
    __table_args__ = (UniqueConstraint("book_id", "author_id", name="uq_book_author"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("book.id", ondelete="CASCADE"))
    author_id: Mapped[int] = mapped_column(ForeignKey("author.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(32), default="author", nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    book: Mapped[Book] = relationship(back_populates="authors")
    author: Mapped[Author] = relationship(back_populates="books")


class AuthorContact(Base):
    """A contact point plus where it came from.

    `source_url` is mandatory in practice: without it you cannot show why you hold
    someone's details, and you cannot honour a request to delete them.
    """

    __tablename__ = "author_contact"
    __table_args__ = (
        UniqueConstraint("author_id", "kind", "value", name="uq_author_contact"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("author.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # email|website|twitter|...
    value: Mapped[str] = mapped_column(String(500), nullable=False)
    source_id: Mapped[str | None] = mapped_column(String(64))
    source_url: Mapped[str | None] = mapped_column(String(1000))
    confidence: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    collected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    author: Mapped[Author] = relationship(back_populates="contacts")


class Lead(Base, TimestampMixin):
    __tablename__ = "lead"
    __table_args__ = (
        UniqueConstraint("author_id", "book_id", name="uq_lead_author_book"),
        Index("ix_lead_score", "score"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("author.id", ondelete="CASCADE"))
    book_id: Mapped[int] = mapped_column(ForeignKey("book.id", ondelete="CASCADE"))
    score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    tier: Mapped[str] = mapped_column(String(8), default="C", nullable=False)
    reasons: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(16), default=LEAD_NEW, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)

    author: Mapped[Author] = relationship()
    book: Mapped[Book] = relationship()


class Suppression(Base):
    """Do-not-contact list, enforced at export time rather than at capture."""

    __tablename__ = "suppression"
    __table_args__ = (UniqueConstraint("kind", "value", name="uq_suppression"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # email|domain|author_name
    value: Mapped[str] = mapped_column(String(500), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
