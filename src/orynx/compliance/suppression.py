"""Do-not-contact enforcement.

Suppression is applied at export rather than at capture, so an opt-out survives
re-crawling: the record stays in the database with its provenance intact, but it
never leaves the building again.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from orynx.db.models import Suppression

KIND_EMAIL = "email"
KIND_DOMAIN = "domain"
KIND_AUTHOR = "author_name"


class SuppressionList:
    """In-memory view of the suppression table, loaded once per export."""

    def __init__(
        self, emails: set[str], domains: set[str], author_names: set[str]
    ) -> None:
        self.emails = emails
        self.domains = domains
        self.author_names = author_names

    @classmethod
    def load(cls, session: Session) -> SuppressionList:
        rows = session.scalars(select(Suppression)).all()
        return cls(
            emails={r.value.lower() for r in rows if r.kind == KIND_EMAIL},
            domains={r.value.lower().lstrip("@") for r in rows if r.kind == KIND_DOMAIN},
            author_names={r.value.lower() for r in rows if r.kind == KIND_AUTHOR},
        )

    def blocks(self, *, author_name: str | None, emails: list[str]) -> str | None:
        """Return the reason this lead is suppressed, or None if it may be exported."""
        if author_name and author_name.lower() in self.author_names:
            return "author on do-not-contact list"
        for email in emails:
            lowered = email.lower()
            if lowered in self.emails:
                return "email on do-not-contact list"
            _, _, domain = lowered.partition("@")
            if domain and domain in self.domains:
                return f"domain {domain} suppressed"
        return None

    def __len__(self) -> int:
        return len(self.emails) + len(self.domains) + len(self.author_names)


def add_suppression(
    session: Session, kind: str, value: str, reason: str | None = None
) -> Suppression:
    value = value.strip().lower()
    existing = session.scalar(
        select(Suppression).where(Suppression.kind == kind, Suppression.value == value)
    )
    if existing is not None:
        return existing
    row = Suppression(kind=kind, value=value, reason=reason)
    session.add(row)
    session.flush()
    return row
