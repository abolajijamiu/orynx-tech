"""Open Library author records.

Two things make this the right first step in the chain. Author pages often carry
an explicit `links` list holding the author's own website, and `remote_ids`
frequently holds a Wikidata Q-id — which lets the next enricher look the person
up by identifier instead of guessing from a name.
"""

from __future__ import annotations

from typing import Any

from orynx.enrich.base import AuthorEnricher, AuthorProfile
from orynx.logging import get_logger
from orynx.textutil import clean_text, normalize_person

log = get_logger(__name__)

SEARCH_URL = "https://openlibrary.org/search/authors.json"
AUTHOR_URL = "https://openlibrary.org/authors/{key}.json"

# Link titles that denote a personal site rather than a shop or a listing.
SITE_TITLE_HINTS = ("website", "official", "homepage", "home page", "blog", "site")

SOCIAL_HOSTS = {
    "twitter.com": "twitter",
    "x.com": "twitter",
    "instagram.com": "instagram",
    "facebook.com": "facebook",
    "linkedin.com": "linkedin",
    "goodreads.com": "goodreads",
    "youtube.com": "youtube",
    "tiktok.com": "tiktok",
    "mastodon.social": "mastodon",
    "bsky.app": "bluesky",
    "substack.com": "substack",
}


class OpenLibraryAuthorEnricher(AuthorEnricher):
    id = "openlibrary"
    name = "Open Library authors"

    async def enrich(
        self, author_name: str, hints: AuthorProfile | None = None
    ) -> AuthorProfile:
        key = (hints.openlibrary_id if hints else None) or await self._find_key(author_name)
        if not key:
            return AuthorProfile()

        try:
            result = await self.client.get(AUTHOR_URL.format(key=key))
        except Exception as exc:
            log.debug("openlibrary author fetch failed for %s: %s", key, exc)
            return AuthorProfile()
        if not result.ok:
            return AuthorProfile()

        try:
            data = result.json()
        except ValueError:
            return AuthorProfile()
        return self._to_profile(data, key, result.url)

    async def _find_key(self, author_name: str) -> str | None:
        """Search by name, accepting only an exact normalised match."""
        try:
            result = await self.client.get(SEARCH_URL, params={"q": author_name})
        except Exception as exc:
            log.debug("openlibrary author search failed for %r: %s", author_name, exc)
            return None
        if not result.ok:
            return None
        try:
            docs = result.json().get("docs") or []
        except ValueError:
            return None

        target = normalize_person(author_name)
        for doc in docs[:5]:
            if normalize_person(doc.get("name") or "") == target:
                return str(doc.get("key") or "").strip() or None
        return None

    def _to_profile(self, data: dict[str, Any], key: str, source_url: str) -> AuthorProfile:
        profile = AuthorProfile(
            openlibrary_id=key,
            source_id=self.id,
            source_url=source_url,
            # An exact name match against a curated bibliographic record is a
            # strong signal, though weaker than an identifier-based lookup.
            confidence=0.75,
        )

        remote = data.get("remote_ids") or {}
        if isinstance(remote, dict):
            wikidata = remote.get("wikidata")
            if isinstance(wikidata, str) and wikidata.startswith("Q"):
                profile.wikidata_id = wikidata
            orcid = remote.get("orcid")
            if isinstance(orcid, str):
                profile.orcid = orcid

        bio = data.get("bio")
        if isinstance(bio, dict):
            bio = bio.get("value")
        profile.description = clean_text(bio) if isinstance(bio, str) else None

        for link in data.get("links") or []:
            if not isinstance(link, dict):
                continue
            url = link.get("url")
            if not isinstance(url, str) or not url.startswith("http"):
                continue
            network = _social_network(url)
            if network:
                profile.socials.setdefault(network, url)
                continue
            title = (link.get("title") or "").lower()
            if profile.website is None and (
                not title or any(hint in title for hint in SITE_TITLE_HINTS)
            ):
                profile.website = url

        return profile


def _social_network(url: str) -> str | None:
    lowered = url.lower()
    for host, network in SOCIAL_HOSTS.items():
        if host in lowered:
            return network
    return None
