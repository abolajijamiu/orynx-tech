"""Author enrichment: turning a name into a way to reach someone.

Extraction gives us names and titles; almost none of that is contactable. These
enrichers close that gap using open identity sources, chained so that each step
hands the next a stronger identifier than a name.

Every field carries the URL it came from, because a contact detail without a
provenance trail cannot be justified or deleted on request.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from orynx.fetch import PoliteClient

# Below this, a match is treated as a different person and discarded. Attaching
# the wrong author's social account to a lead is worse than having no contact.
MIN_CONFIDENCE = 0.55


@dataclass(slots=True)
class AuthorProfile:
    """What one enricher learned about an author."""

    website: str | None = None
    socials: dict[str, str] = field(default_factory=dict)
    emails: list[str] = field(default_factory=list)
    orcid: str | None = None
    wikidata_id: str | None = None
    openlibrary_id: str | None = None
    description: str | None = None
    source_url: str | None = None
    source_id: str | None = None
    confidence: float = 0.0
    # value -> (source_id, source_url). A merged profile holds facts from several
    # enrichers, so provenance is recorded per value rather than per profile;
    # otherwise a contact from Wikidata would be filed under whichever source
    # happened to be most confident overall.
    origins: dict[str, tuple[str | None, str | None]] = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        return not (self.website or self.socials or self.emails or self.orcid)

    def origin_of(self, value: str) -> tuple[str | None, str | None]:
        return self.origins.get(value, (self.source_id, self.source_url))

    def contact_pairs(self) -> list[tuple[str, str, str | None, str | None]]:
        """Flatten into (kind, value, source_id, source_url) rows for storage."""
        values: list[tuple[str, str]] = []
        if self.website:
            values.append(("website", self.website))
        values.extend(("email", email) for email in self.emails)
        values.extend((network, handle) for network, handle in self.socials.items())
        if self.orcid:
            values.append(("orcid", self.orcid))

        rows = []
        for kind, value in values:
            source_id, source_url = self.origin_of(value)
            rows.append((kind, value, source_id, source_url))
        return rows


def merge_profiles(profiles: list[AuthorProfile]) -> AuthorProfile:
    """Combine profiles, preferring values from the most confident source.

    Each adopted value keeps a note of which enricher supplied it, so the merged
    profile can still answer "where did this address come from?".
    """
    ordered = sorted(profiles, key=lambda p: p.confidence, reverse=True)
    merged = AuthorProfile()

    def claim(value: str | None, profile: AuthorProfile) -> None:
        if value and value not in merged.origins:
            merged.origins[value] = profile.origin_of(value)

    for profile in ordered:
        if merged.website is None and profile.website:
            merged.website = profile.website
        claim(profile.website, profile)

        if merged.orcid is None and profile.orcid:
            merged.orcid = profile.orcid
        claim(profile.orcid, profile)

        merged.wikidata_id = merged.wikidata_id or profile.wikidata_id
        merged.openlibrary_id = merged.openlibrary_id or profile.openlibrary_id
        merged.description = merged.description or profile.description
        merged.source_id = merged.source_id or profile.source_id
        merged.source_url = merged.source_url or profile.source_url

        for network, handle in profile.socials.items():
            merged.socials.setdefault(network, handle)
            claim(handle, profile)
        for email in profile.emails:
            if email not in merged.emails:
                merged.emails.append(email)
            claim(email, profile)

        merged.confidence = max(merged.confidence, profile.confidence)
    return merged


class AuthorEnricher(ABC):
    """One identity source."""

    id: str
    name: str

    def __init__(self, client: PoliteClient) -> None:
        self.client = client

    @abstractmethod
    async def enrich(self, author_name: str, hints: AuthorProfile | None = None) -> AuthorProfile:
        """Look up an author. Must never raise; return an empty profile instead.

        `hints` carries identifiers found by earlier enrichers in the chain, which
        is what lets a later step skip a risky name search.
        """
