"""Wikidata author identities.

Wikidata holds official websites, social handles and ORCIDs for a large number of
published authors, all under an open licence and a keyless API.

The risk is misidentification: a name search for a common name will happily
return a footballer. Two defences apply. When an earlier enricher supplied a
Q-id the entity is fetched directly and no guessing occurs. When only a name is
available, a candidate is accepted only if it is a human whose occupations
include writing, and the confidence is recorded lower.
"""

from __future__ import annotations

from typing import Any

from orynx.enrich.base import AuthorEnricher, AuthorProfile
from orynx.logging import get_logger
from orynx.textutil import clean_text, normalize_person

log = get_logger(__name__)

API_URL = "https://www.wikidata.org/w/api.php"

INSTANCE_OF = "P31"
HUMAN = "Q5"
OCCUPATION = "P106"

# Occupations that make someone plausibly the author of a book.
WRITING_OCCUPATIONS = {
    "Q36180",    # writer
    "Q482980",   # author
    "Q6625963",  # novelist
    "Q49757",    # poet
    "Q214917",   # playwright
    "Q11774202", # essayist
    "Q12144794", # non-fiction writer
    "Q4853732",  # children's writer
    "Q28389",    # screenwriter
    "Q1930187",  # journalist
    "Q1622272",  # university teacher (academic monographs)
    "Q205375",   # historian
    "Q4964182",  # philosopher
}

# Properties worth reading, and how each is stored.
WEBSITE = "P856"
ORCID = "P496"
SOCIAL_PROPERTIES = {
    "P2002": ("twitter", "https://twitter.com/{}"),
    "P2003": ("instagram", "https://instagram.com/{}"),
    "P2013": ("facebook", "https://facebook.com/{}"),
    "P2397": ("youtube", "https://youtube.com/channel/{}"),
    "P4033": ("mastodon", "https://{}"),
    "P6634": ("linkedin", "https://linkedin.com/in/{}"),
    "P2963": ("goodreads", "https://goodreads.com/author/show/{}"),
    "P3258": ("substack", "https://{}.substack.com"),
}


class WikidataEnricher(AuthorEnricher):
    id = "wikidata"
    name = "Wikidata"

    async def enrich(
        self, author_name: str, hints: AuthorProfile | None = None
    ) -> AuthorProfile:
        entity_id = hints.wikidata_id if hints else None
        verified_by_id = entity_id is not None

        if verified_by_id:
            entity = await self._get_entity(entity_id)
            if entity is None:
                return AuthorProfile()
            return self._to_profile(entity, entity_id, verified_by_id=True)

        # A common name returns several people and the author is rarely first,
        # so every label match is checked until one is plausibly a writer.
        for candidate in await self._search(author_name):
            entity = await self._get_entity(candidate)
            if entity is None:
                continue
            if not _label_matches(entity, author_name):
                continue
            if not _is_plausible_author(entity):
                log.debug("wikidata %s is not a writing human; trying next", candidate)
                continue
            return self._to_profile(entity, candidate, verified_by_id=False)

        return AuthorProfile()

    async def _search(self, author_name: str) -> list[str]:
        """Return every candidate whose label matches the name, best first."""
        try:
            result = await self.client.get(
                API_URL,
                params={
                    "action": "wbsearchentities",
                    "search": author_name,
                    "language": "en",
                    "type": "item",
                    "limit": 5,
                    "format": "json",
                },
            )
        except Exception as exc:
            log.debug("wikidata search failed for %r: %s", author_name, exc)
            return []
        if not result.ok:
            return []
        try:
            hits = result.json().get("search") or []
        except ValueError:
            return []

        target = normalize_person(author_name)
        return [
            hit["id"]
            for hit in hits
            if hit.get("id") and normalize_person(hit.get("label") or "") == target
        ]

    async def _get_entity(self, entity_id: str) -> dict[str, Any] | None:
        try:
            result = await self.client.get(
                API_URL,
                params={
                    "action": "wbgetentities",
                    "ids": entity_id,
                    "props": "claims|labels|descriptions",
                    "languages": "en",
                    "format": "json",
                },
            )
        except Exception as exc:
            log.debug("wikidata entity fetch failed for %s: %s", entity_id, exc)
            return None
        if not result.ok:
            return None
        try:
            return (result.json().get("entities") or {}).get(entity_id)
        except ValueError:
            return None

    def _to_profile(
        self, entity: dict[str, Any], entity_id: str, verified_by_id: bool
    ) -> AuthorProfile:
        profile = AuthorProfile(
            wikidata_id=entity_id,
            source_id=self.id,
            source_url=f"https://www.wikidata.org/wiki/{entity_id}",
            # An identifier handed over by Open Library is not a guess; a name
            # search that survived the occupation check still might be.
            confidence=0.95 if verified_by_id else 0.7,
        )

        websites = _string_claims(entity, WEBSITE)
        if websites:
            profile.website = websites[0]

        orcids = _string_claims(entity, ORCID)
        if orcids:
            profile.orcid = orcids[0]

        for prop, (network, template) in SOCIAL_PROPERTIES.items():
            values = _string_claims(entity, prop)
            if values:
                profile.socials[network] = template.format(values[0])

        description = ((entity.get("descriptions") or {}).get("en") or {}).get("value")
        profile.description = clean_text(description)
        return profile


def _claims(entity: dict[str, Any], prop: str) -> list[dict[str, Any]]:
    return (entity.get("claims") or {}).get(prop) or []


def _string_claims(entity: dict[str, Any], prop: str) -> list[str]:
    values: list[str] = []
    for claim in _claims(entity, prop):
        datavalue = (claim.get("mainsnak") or {}).get("datavalue") or {}
        value = datavalue.get("value")
        if isinstance(value, str) and value:
            values.append(value)
    return values


def _entity_ids(entity: dict[str, Any], prop: str) -> set[str]:
    ids: set[str] = set()
    for claim in _claims(entity, prop):
        datavalue = (claim.get("mainsnak") or {}).get("datavalue") or {}
        value = datavalue.get("value")
        if isinstance(value, dict) and value.get("id"):
            ids.add(str(value["id"]))
    return ids


def _is_plausible_author(entity: dict[str, Any]) -> bool:
    if HUMAN not in _entity_ids(entity, INSTANCE_OF):
        return False
    return bool(_entity_ids(entity, OCCUPATION) & WRITING_OCCUPATIONS)


def _label_matches(entity: dict[str, Any], author_name: str) -> bool:
    label = ((entity.get("labels") or {}).get("en") or {}).get("value") or ""
    return normalize_person(label) == normalize_person(author_name)
