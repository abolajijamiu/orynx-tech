"""Running the enrichment chain and storing what it finds.

Order matters. Open Library goes first because it often yields a Wikidata Q-id,
which turns Wikidata's risky name search into an exact lookup. The author's own
website is visited last, once an earlier step has found its address.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from orynx.db.models import Author
from orynx.enrich.base import MIN_CONFIDENCE, AuthorEnricher, AuthorProfile, merge_profiles
from orynx.enrich.openlibrary import OpenLibraryAuthorEnricher
from orynx.enrich.website import WebsiteEnricher
from orynx.enrich.wikidata import WikidataEnricher
from orynx.fetch import PoliteClient
from orynx.logging import get_logger
from orynx.pipeline.dedupe import record_contact

log = get_logger(__name__)

DEFAULT_CHAIN: list[type[AuthorEnricher]] = [
    OpenLibraryAuthorEnricher,
    WikidataEnricher,
    WebsiteEnricher,
]


@dataclass
class EnrichmentStats:
    attempted: int = 0
    enriched: int = 0
    contacts_added: int = 0
    websites_found: int = 0
    emails_found: int = 0
    by_source: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "attempted": self.attempted,
            "enriched": self.enriched,
            "contacts_added": self.contacts_added,
            "websites_found": self.websites_found,
            "emails_found": self.emails_found,
            "by_source": self.by_source,
        }


def build_chain(client: PoliteClient) -> list[AuthorEnricher]:
    return [cls(client) for cls in DEFAULT_CHAIN]


async def enrich_author(
    client: PoliteClient,
    author_name: str,
    *,
    chain: list[AuthorEnricher] | None = None,
    seed: AuthorProfile | None = None,
) -> AuthorProfile:
    """Run every enricher, feeding each one what the previous ones learned."""
    chain = chain if chain is not None else build_chain(client)
    accumulated = seed or AuthorProfile()
    found: list[AuthorProfile] = []

    for enricher in chain:
        try:
            profile = await enricher.enrich(author_name, hints=accumulated)
        except Exception as exc:  # an enricher must never break the run
            log.debug("enricher %s failed for %r: %s", enricher.id, author_name, exc)
            continue
        if profile.is_empty and not profile.wikidata_id and not profile.openlibrary_id:
            continue
        found.append(profile)
        # Feed identifiers forward so the next enricher can skip guessing.
        accumulated = merge_profiles([accumulated, profile])

    if not found:
        return AuthorProfile()
    merged = merge_profiles(found)
    return merged if merged.confidence >= MIN_CONFIDENCE else AuthorProfile()


async def enrich_authors(
    session: Session,
    client: PoliteClient,
    authors: list[Author],
    *,
    chain: list[AuthorEnricher] | None = None,
) -> EnrichmentStats:
    """Enrich a batch of authors and persist the contacts with provenance."""
    stats = EnrichmentStats()
    chain = chain if chain is not None else build_chain(client)

    for author in authors:
        stats.attempted += 1
        seed = AuthorProfile(website=author.website) if author.website else None
        profile = await enrich_author(client, author.display_name, chain=chain, seed=seed)
        if profile.is_empty:
            continue

        stats.enriched += 1
        if profile.source_id:
            stats.by_source[profile.source_id] = stats.by_source.get(profile.source_id, 0) + 1

        if profile.website and not author.website:
            author.website = profile.website[:500]
            stats.websites_found += 1
        if profile.description and not author.bio:
            author.bio = profile.description

        for kind, value, source_id, source_url in profile.contact_pairs():
            created = record_contact(
                session, author, kind, value,
                source_id=source_id or profile.source_id,
                source_url=source_url or profile.source_url,
                confidence=profile.confidence,
            )
            if created is not None:
                stats.contacts_added += 1
                if kind == "email":
                    stats.emails_found += 1

        session.flush()

    session.commit()
    return stats
