"""Author enrichment: identity resolution, the chain, and misidentification guards."""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select

from orynx.db.models import Author, AuthorContact
from orynx.enrich.base import AuthorProfile, merge_profiles
from orynx.enrich.openlibrary import OpenLibraryAuthorEnricher
from orynx.enrich.runner import enrich_author, enrich_authors
from orynx.enrich.website import WebsiteEnricher, parse_contacts
from orynx.enrich.wikidata import WikidataEnricher
from orynx.fetch import PoliteClient
from tests.conftest import fixture_text


def routed_client(settings, routes, *, recorder=None):
    """Like make_client, but can record the URLs requested."""
    ordered = sorted(routes.items(), key=lambda kv: len(kv[0]), reverse=True)

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if recorder is not None:
            recorder.append(url)
        for pattern, (status, body) in ordered:
            if pattern in url:
                return httpx.Response(status, text=body)
        return httpx.Response(404, text="not found")

    return PoliteClient(
        settings=settings, use_cache=False, obey_robots=False,
        transport=httpx.MockTransport(handler),
    )


OL_ROUTES = {
    "openlibrary.org/search/authors.json": (
        200, fixture_text("api", "openlibrary_author_search.json")
    ),
    "openlibrary.org/authors/OL1234A.json": (
        200, fixture_text("api", "openlibrary_author.json")
    ),
}
WD_ENTITY = fixture_text("api", "wikidata_entity_author.json")
WD_FOOTBALLER = fixture_text("api", "wikidata_entity_footballer.json")
WD_SEARCH = fixture_text("api", "wikidata_search.json")


# --------------------------------------------------------------------------- #
# Open Library author records
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_openlibrary_extracts_website_socials_and_wikidata_id(settings):
    client = routed_client(settings, OL_ROUTES)
    try:
        profile = await OpenLibraryAuthorEnricher(client).enrich("Amara Nwosu")
    finally:
        await client.aclose()

    assert profile.website == "https://amara.example"
    assert profile.wikidata_id == "Q7654321"
    assert profile.openlibrary_id == "OL1234A"
    assert profile.socials["twitter"] == "https://twitter.com/amarawrites"
    assert profile.socials["goodreads"].endswith("999.Amara")
    assert "Nigerian novelist" in profile.description
    assert profile.confidence >= 0.7


@pytest.mark.asyncio
async def test_openlibrary_requires_an_exact_name_match(settings):
    """The fixture's first hit is a different author; only an exact match counts."""
    client = routed_client(settings, OL_ROUTES)
    try:
        profile = await OpenLibraryAuthorEnricher(client).enrich("Chidi Okonkwo")
    finally:
        await client.aclose()
    assert profile.is_empty


@pytest.mark.asyncio
async def test_openlibrary_survives_a_failing_api(settings):
    client = routed_client(settings, {"openlibrary.org": (500, "boom")})
    try:
        profile = await OpenLibraryAuthorEnricher(client).enrich("Amara Nwosu")
    finally:
        await client.aclose()
    assert profile.is_empty


# --------------------------------------------------------------------------- #
# Wikidata
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_wikidata_direct_lookup_is_high_confidence(settings):
    calls: list[str] = []
    client = routed_client(settings, {"wbgetentities": (200, WD_ENTITY)}, recorder=calls)
    try:
        hints = AuthorProfile(wikidata_id="Q7654321")
        profile = await WikidataEnricher(client).enrich("Amara Nwosu", hints=hints)
    finally:
        await client.aclose()

    assert profile.website == "https://amara.example"
    assert profile.socials["twitter"] == "https://twitter.com/amarawrites"
    assert profile.socials["instagram"] == "https://instagram.com/amara.writes"
    assert profile.orcid == "0000-0002-1825-0097"
    assert profile.confidence == 0.95
    # A known id must not trigger a name search.
    assert not any("wbsearchentities" in url for url in calls)


@pytest.mark.asyncio
async def test_wikidata_name_search_rejects_the_wrong_profession(settings):
    """The search fixture returns the footballer first; occupation must filter it."""
    client = routed_client(
        settings,
        {"wbsearchentities": (200, WD_SEARCH), "wbgetentities": (200, WD_FOOTBALLER)},
    )
    try:
        profile = await WikidataEnricher(client).enrich("Amara Nwosu")
    finally:
        await client.aclose()

    assert profile.is_empty
    assert profile.website != "https://wrong-person.example"


@pytest.mark.asyncio
async def test_wikidata_name_search_accepts_a_writer(settings):
    client = routed_client(
        settings,
        {"wbsearchentities": (200, WD_SEARCH), "wbgetentities": (200, WD_ENTITY)},
    )
    try:
        profile = await WikidataEnricher(client).enrich("Amara Nwosu")
    finally:
        await client.aclose()

    assert profile.website == "https://amara.example"
    # A name search is inherently less certain than an identifier lookup.
    assert profile.confidence == 0.7


@pytest.mark.asyncio
async def test_wikidata_name_search_rejects_a_label_mismatch(settings):
    client = routed_client(
        settings,
        {"wbsearchentities": (200, WD_SEARCH), "wbgetentities": (200, WD_ENTITY)},
    )
    try:
        profile = await WikidataEnricher(client).enrich("Someone Else")
    finally:
        await client.aclose()
    assert profile.is_empty


@pytest.mark.asyncio
async def test_wikidata_handles_a_missing_entity(settings):
    client = routed_client(settings, {"wbgetentities": (200, '{"entities": {}}')})
    try:
        profile = await WikidataEnricher(client).enrich(
            "Amara Nwosu", hints=AuthorProfile(wikidata_id="Q7654321")
        )
    finally:
        await client.aclose()
    assert profile.is_empty


# --------------------------------------------------------------------------- #
# The author's own site
# --------------------------------------------------------------------------- #

def test_parse_contacts_keeps_own_domain_and_drops_third_party_inboxes():
    """"hello@" on the author's own domain is theirs; a publisher's inbox is not."""
    findings = parse_contacts(fixture_text("html", "author_site.html"), "https://amara.example")
    assert findings.emails == ["hello@amara.example"]
    assert "info@somepublisher.com" not in findings.emails
    assert findings.socials["instagram"].endswith("amara.writes")


def test_parse_contacts_keeps_a_personal_address_on_another_domain():
    html = '<body><a href="mailto:amara.nwosu@gmail.com">mail</a></body>'
    assert parse_contacts(html, "https://amara.example").emails == ["amara.nwosu@gmail.com"]


def test_parse_contacts_drops_a_generic_inbox_on_another_domain():
    html = '<body><a href="mailto:info@bigpublisher.com">mail</a></body>'
    assert parse_contacts(html, "https://amara.example").emails == []


@pytest.mark.asyncio
async def test_website_enricher_does_nothing_without_a_known_site(settings):
    client = routed_client(settings, {})
    try:
        profile = await WebsiteEnricher(client).enrich("Amara Nwosu")
    finally:
        await client.aclose()
    assert profile.is_empty


@pytest.mark.asyncio
async def test_website_enricher_reads_a_known_site(settings):
    client = routed_client(
        settings, {"amara.example": (200, fixture_text("html", "author_site.html"))}
    )
    try:
        profile = await WebsiteEnricher(client).enrich(
            "Amara Nwosu", hints=AuthorProfile(website="https://amara.example")
        )
    finally:
        await client.aclose()
    assert profile.emails == ["hello@amara.example"]


# --------------------------------------------------------------------------- #
# Merging and the full chain
# --------------------------------------------------------------------------- #

def test_merge_prefers_the_more_confident_source():
    low = AuthorProfile(website="https://low.example", confidence=0.4)
    high = AuthorProfile(website="https://high.example", confidence=0.9)
    assert merge_profiles([low, high]).website == "https://high.example"


def test_merge_unions_socials_and_emails():
    a = AuthorProfile(socials={"twitter": "t"}, emails=["a@x.test"], confidence=0.5)
    b = AuthorProfile(socials={"instagram": "i"}, emails=["a@x.test", "b@x.test"], confidence=0.6)
    merged = merge_profiles([a, b])
    assert set(merged.socials) == {"twitter", "instagram"}
    assert merged.emails == ["b@x.test", "a@x.test"] or merged.emails == ["a@x.test", "b@x.test"]
    assert len(merged.emails) == 2


@pytest.mark.asyncio
async def test_chain_uses_openlibrarys_id_to_avoid_a_wikidata_search(settings):
    calls: list[str] = []
    routes = {
        **OL_ROUTES,
        "wbgetentities": (200, WD_ENTITY),
        "wbsearchentities": (200, WD_SEARCH),
        "amara.example": (200, fixture_text("html", "author_site.html")),
    }
    client = routed_client(settings, routes, recorder=calls)
    try:
        profile = await enrich_author(client, "Amara Nwosu")
    finally:
        await client.aclose()

    assert not any("wbsearchentities" in url for url in calls), (
        "Open Library supplied a Q-id, so no name search should have run"
    )
    assert profile.website == "https://amara.example"
    assert profile.orcid == "0000-0002-1825-0097"
    assert profile.emails == ["hello@amara.example"]
    assert profile.confidence >= 0.9


@pytest.mark.asyncio
async def test_chain_returns_empty_when_nothing_is_found(settings):
    client = routed_client(settings, {})
    try:
        assert (await enrich_author(client, "Nobody Atall")).is_empty
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_enrich_authors_persists_contacts_with_provenance(session, settings):
    author = Author(
        display_name="Amara Nwosu", normalized_name="amara nwosu", dedupe_key="nwosu:a"
    )
    session.add(author)
    session.commit()

    routes = {
        **OL_ROUTES,
        "wbgetentities": (200, WD_ENTITY),
        "amara.example": (200, fixture_text("html", "author_site.html")),
    }
    client = routed_client(settings, routes)
    try:
        stats = await enrich_authors(session, client, [author])
    finally:
        await client.aclose()

    assert stats.enriched == 1
    assert stats.websites_found == 1
    assert stats.emails_found == 1

    contacts = session.scalars(
        select(AuthorContact).where(AuthorContact.author_id == author.id)
    ).all()
    kinds = {c.kind for c in contacts}
    assert {"website", "email", "twitter", "orcid"} <= kinds
    assert author.website == "https://amara.example"
    # Every stored contact must say where it came from.
    assert all(c.source_url for c in contacts)
    assert all(c.source_id for c in contacts)


@pytest.mark.asyncio
async def test_enrich_authors_is_idempotent(session, settings):
    author = Author(
        display_name="Amara Nwosu", normalized_name="amara nwosu", dedupe_key="nwosu:a"
    )
    session.add(author)
    session.commit()

    routes = {**OL_ROUTES, "wbgetentities": (200, WD_ENTITY),
              "amara.example": (200, fixture_text("html", "author_site.html"))}
    client = routed_client(settings, routes)
    try:
        await enrich_authors(session, client, [author])
        first = len(session.scalars(select(AuthorContact)).all())
        await enrich_authors(session, client, [author])
        second = len(session.scalars(select(AuthorContact)).all())
    finally:
        await client.aclose()
    assert first == second


@pytest.mark.asyncio
async def test_wikidata_falls_through_to_the_writer_when_listed_second(settings):
    """The search fixture lists the footballer first; the novelist must still win."""
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "wbsearchentities" in url:
            return httpx.Response(200, text=WD_SEARCH)
        if "Q1111111" in url:
            return httpx.Response(200, text=WD_FOOTBALLER)
        if "Q7654321" in url:
            return httpx.Response(200, text=WD_ENTITY)
        return httpx.Response(404)

    client = PoliteClient(
        settings=settings, use_cache=False, obey_robots=False,
        transport=httpx.MockTransport(handler),
    )
    try:
        profile = await WikidataEnricher(client).enrich("Amara Nwosu")
    finally:
        await client.aclose()

    assert profile.wikidata_id == "Q7654321"
    assert profile.website == "https://amara.example"


@pytest.mark.asyncio
async def test_merged_contacts_are_filed_under_the_enricher_that_found_them(
    session, settings
):
    """A contact from the website must not be attributed to Wikidata, or vice versa."""
    author = Author(
        display_name="Amara Nwosu", normalized_name="amara nwosu", dedupe_key="nwosu:a"
    )
    session.add(author)
    session.commit()

    routes = {
        **OL_ROUTES,
        "wbgetentities": (200, WD_ENTITY),
        "amara.example": (200, fixture_text("html", "author_site.html")),
    }
    client = routed_client(settings, routes)
    try:
        await enrich_authors(session, client, [author])
    finally:
        await client.aclose()

    by_kind = {
        c.kind: c
        for c in session.scalars(
            select(AuthorContact).where(AuthorContact.author_id == author.id)
        ).all()
    }
    # The email exists only on the author's own page.
    assert by_kind["email"].source_id == "website"
    assert "amara.example" in by_kind["email"].source_url
    # The ORCID exists only in Wikidata.
    assert by_kind["orcid"].source_id == "wikidata"
    assert "wikidata.org" in by_kind["orcid"].source_url
