"""Adapter tests: API parsing and the recipe engine, all served from fixtures."""

from __future__ import annotations

import json

import pytest

from orynx.sources.api.googlebooks import GoogleBooksSource
from orynx.sources.api.openlibrary import OpenLibrarySource
from orynx.sources.html.generic import RecipeSource
from orynx.sources.html.recipe import Recipe
from orynx.sources.registry import SourceRegistry
from tests.conftest import fixture_json, fixture_text, make_client


async def collect(iterator):
    return [item async for item in iterator]


@pytest.mark.asyncio
async def test_openlibrary_parses_search_results(settings):
    payload = json.dumps(fixture_json("api", "openlibrary_search.json"))
    client = make_client(settings, {"openlibrary.org/search.json": (200, payload)})
    try:
        books = await collect(OpenLibrarySource(client).search("test", limit=2))
    finally:
        await client.aclose()

    assert len(books) == 2
    first = books[0]
    assert first.title == "The Quiet Harbour"
    assert first.subtitle == "A Novel"
    assert first.isbn13 == "9781234567897"
    assert [a.name for a in first.authors] == ["Amara Nwosu"]
    assert first.publisher == "Koehler Books"
    assert first.ratings_count == 4
    assert first.url == "https://openlibrary.org/works/OL1W"
    assert books[1].authors[1].name == "Peter Blake"


@pytest.mark.asyncio
async def test_googlebooks_parses_and_strips_description_markup(settings):
    payload = json.dumps(fixture_json("api", "googlebooks_search.json"))
    client = make_client(settings, {"googleapis.com/books": (200, payload)})
    try:
        books = await collect(GoogleBooksSource(client).search("test", limit=5))
    finally:
        await client.aclose()

    assert len(books) == 1
    book = books[0]
    assert book.isbn13 == "9781234567897"
    assert book.isbn10 == "0306406152"
    assert book.description == "A debut novel about home."
    assert book.published_date == "2024-03-15"


@pytest.mark.asyncio
async def test_search_stops_at_limit(settings):
    payload = json.dumps(fixture_json("api", "openlibrary_search.json"))
    client = make_client(settings, {"openlibrary.org": (200, payload)})
    try:
        books = await collect(OpenLibrarySource(client).search("test", limit=1))
    finally:
        await client.aclose()
    assert len(books) == 1


PAGINATED_RECIPE = {
    "id": "presstest",
    "name": "Press Test",
    "kind": "publisher",
    "homepage": "https://press.test",
    "discover": {
        "strategy": "paginate",
        "url_template": "https://press.test/books?page={page}",
        "max_pages": 2,
    },
    "listing": {
        "item_selector": "article.book-card",
        "fields": {"detail_url": {"css": "a.title", "attr": "href"}},
    },
    "detail": {
        "prefer_jsonld": True,
        "fields": {
            "title": {"css": "h1.book-title"},
            "authors": {"css": ".author-name", "many": True},
            "isbn": {"css": "[itemprop=isbn]", "regex": r"([0-9Xx\-]{10,17})",
                     "transform": ["isbn"]},
            "published_date": {"css": ".pub-date"},
        },
    },
    "constants": {"publisher": "Press Test"},
}


@pytest.mark.asyncio
async def test_recipe_source_crawls_listing_then_details(settings):
    client = make_client(
        settings,
        {
            "press.test/books?page=1": (200, fixture_text("html", "listing.html")),
            "press.test/books?page=2": (200, "<html><body></body></html>"),
            "/books/quiet-harbour": (200, fixture_text("html", "detail_jsonld.html")),
            "/books/tidewater": (200, fixture_text("html", "detail_selectors.html")),
        },
    )
    try:
        source = RecipeSource(client, Recipe.model_validate(PAGINATED_RECIPE))
        books = await collect(source.crawl())
    finally:
        await client.aclose()

    assert len(books) == 2
    by_title = {b.title: b for b in books}

    # This page carried JSON-LD, so no selectors were needed for it.
    harbour = by_title["The Quiet Harbour"]
    assert harbour.isbn13 == "9781234567897"
    assert [a.name for a in harbour.authors] == ["Amara Nwosu"]
    assert harbour.publisher == "Koehler Books"

    # This one had no JSON-LD and was read entirely through selectors.
    tidewater = by_title["Tidewater"]
    assert tidewater.isbn13 == "9780306406157"
    assert [a.name for a in tidewater.authors] == ["Amara Nwosu", "Peter Blake"]
    assert tidewater.published_date == "Published 12 June 2019"
    assert tidewater.publisher == "Press Test"  # supplied by constants


@pytest.mark.asyncio
async def test_selectors_override_jsonld(settings):
    recipe = dict(PAGINATED_RECIPE)
    recipe["detail"] = {"prefer_jsonld": True, "fields": {"title": {"const": "Overridden"}}}
    client = make_client(
        settings,
        {
            "press.test/books?page=1": (200, fixture_text("html", "listing.html")),
            "press.test/books?page=2": (200, "<html></html>"),
            "/books/": (200, fixture_text("html", "detail_jsonld.html")),
        },
    )
    try:
        source = RecipeSource(client, Recipe.model_validate(recipe))
        books = await collect(source.crawl(limit=1))
    finally:
        await client.aclose()
    assert books[0].title == "Overridden"


@pytest.mark.asyncio
async def test_sitemap_recipe_treats_entries_as_detail_pages(settings):
    recipe = Recipe.model_validate(
        {
            "id": "sitemaptest",
            "name": "Sitemap Test",
            "homepage": "https://press.test",
            "discover": {
                "strategy": "sitemap",
                "sitemap_url": "https://press.test/sitemap.xml",
                "url_pattern": "/books/",
                "sitemap_yields": "detail",
            },
        }
    )
    client = make_client(
        settings,
        {
            "press.test/sitemap.xml": (200, fixture_text("html", "sitemap.xml")),
            "/books/quiet-harbour": (200, fixture_text("html", "detail_jsonld.html")),
            "/books/tidewater": (200, fixture_text("html", "detail_jsonld.html")),
        },
    )
    try:
        books = await collect(RecipeSource(client, recipe).crawl())
    finally:
        await client.aclose()

    # /about is filtered out by url_pattern, leaving the two book pages.
    assert len(books) == 2


@pytest.mark.asyncio
async def test_recipe_marked_not_permitted_yields_nothing(settings):
    recipe = dict(PAGINATED_RECIPE)
    recipe["permitted"] = False
    recipe["permitted_note"] = "terms forbid automated access"
    client = make_client(settings, {"press.test": (200, fixture_text("html", "listing.html"))})
    try:
        books = await collect(RecipeSource(client, Recipe.model_validate(recipe)).crawl())
    finally:
        await client.aclose()
    assert books == []


@pytest.mark.asyncio
async def test_crawl_survives_a_failing_detail_page(settings):
    client = make_client(
        settings,
        {
            "press.test/books?page=1": (200, fixture_text("html", "listing.html")),
            "press.test/books?page=2": (200, "<html></html>"),
            "/books/quiet-harbour": (200, fixture_text("html", "detail_jsonld.html")),
            "/books/tidewater": (500, "server error"),
        },
    )
    try:
        source = RecipeSource(client, Recipe.model_validate(PAGINATED_RECIPE))
        books = await collect(source.crawl())
    finally:
        await client.aclose()

    # The 500 falls back to the listing row, which carried no title, so it drops.
    assert [b.title for b in books] == ["The Quiet Harbour"]


def test_registry_lists_builtin_and_recipe_sources(settings):
    registry = SourceRegistry(recipe_dir=settings.recipe_dir)
    ids = registry.ids(enabled_only=False)
    assert {"openlibrary", "googlebooks", "crossref", "openalex"} <= set(ids)
    assert "koehlerbooks" in ids


def test_registry_rejects_unknown_source(settings):
    registry = SourceRegistry(recipe_dir=settings.recipe_dir)
    with pytest.raises(KeyError, match="unknown source"):
        registry.build("does-not-exist", client=None)


def test_shipped_recipes_all_validate(settings):
    registry = SourceRegistry(recipe_dir=settings.recipe_dir)
    assert len(registry.recipes) >= 3
