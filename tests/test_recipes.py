"""Recipe schema validation and the guarantees it enforces."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from orynx.sources.html.recipe import Recipe, load_recipes

BASE = {
    "id": "x",
    "name": "X",
    "discover": {"strategy": "paginate", "url_template": "https://x.test/p/{page}"},
    "listing": {"item_selector": "article"},
}


def test_minimal_recipe_validates():
    assert Recipe.model_validate(BASE).id == "x"


def test_paginate_requires_a_page_placeholder():
    broken = {**BASE, "discover": {"strategy": "paginate", "url_template": "https://x.test/p"}}
    with pytest.raises(ValidationError, match=r"\{page\}"):
        Recipe.model_validate(broken)


def test_static_strategy_requires_urls():
    with pytest.raises(ValidationError, match="urls"):
        Recipe.model_validate({**BASE, "discover": {"strategy": "static"}})


def test_sitemap_strategy_requires_a_sitemap_url():
    with pytest.raises(ValidationError, match="sitemap_url"):
        Recipe.model_validate({**BASE, "discover": {"strategy": "sitemap"}})


def test_listing_is_required_unless_the_sitemap_yields_detail_pages():
    no_listing = {k: v for k, v in BASE.items() if k != "listing"}
    with pytest.raises(ValidationError, match="item_selector"):
        Recipe.model_validate(no_listing)

    sitemap = {
        **no_listing,
        "discover": {
            "strategy": "sitemap",
            "sitemap_url": "https://x.test/sitemap.xml",
            "sitemap_yields": "detail",
        },
    }
    assert Recipe.model_validate(sitemap).listing.item_selector is None


def test_ignoring_robots_demands_a_written_reason():
    with pytest.raises(ValidationError, match="robots_override_reason"):
        Recipe.model_validate({**BASE, "politeness": {"obey_robots": False}})

    allowed = Recipe.model_validate(
        {
            **BASE,
            "politeness": {"obey_robots": False, "robots_override_reason": "own site"},
        }
    )
    assert allowed.politeness.obey_robots is False


def test_field_spec_needs_a_value_source():
    broken = {**BASE, "detail": {"fields": {"title": {"attr": "href"}}}}
    with pytest.raises(ValidationError, match="css, const, jsonld"):
        Recipe.model_validate(broken)


def test_shipped_recipes_load_and_are_documented(settings):
    recipes = load_recipes(settings.recipe_dir)
    assert recipes, "no recipes shipped"
    for recipe in recipes:
        assert recipe.homepage, f"{recipe.id} has no homepage"
        assert recipe.permitted_note, f"{recipe.id} has no permission note"
        assert 0.0 <= recipe.trust <= 1.0


def test_template_is_not_loaded_as_a_recipe(settings):
    assert "example-press" not in {r.id for r in load_recipes(settings.recipe_dir)}
