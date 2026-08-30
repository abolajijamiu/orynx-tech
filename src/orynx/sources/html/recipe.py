"""Declarative site definitions.

Adding a platform should be a YAML file, not a Python module. A recipe describes
three things: how to discover listing pages, how to pull book links off them, and
how to read a detail page. Anything a recipe cannot express falls back to a
hand-written adapter in `sources/api/`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, model_validator


class FieldSpec(BaseModel):
    """How to pull one value out of a page."""

    css: str | None = None
    attr: str | None = None  # read an attribute instead of the text
    regex: str | None = None  # first capture group wins
    many: bool = False
    const: Any = None  # fixed value, e.g. a publisher name the page never prints
    default: Any = None
    join: str | None = None  # combine `many` matches into one string
    index: int | None = None  # pick the nth match
    jsonld: str | None = None  # dotted path into schema.org JSON-LD
    transform: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _require_a_source(self) -> FieldSpec:
        if self.css is None and self.const is None and self.jsonld is None:
            raise ValueError("field spec needs one of: css, const, jsonld")
        return self


class Politeness(BaseModel):
    rate_limit_rps: float | None = None
    obey_robots: bool = True
    # Overriding robots is occasionally legitimate (your own site, a written
    # agreement) but never accidental, so it demands a stated reason.
    robots_override_reason: str | None = None

    @model_validator(mode="after")
    def _justify_override(self) -> Politeness:
        if not self.obey_robots and not self.robots_override_reason:
            raise ValueError("obey_robots: false requires robots_override_reason")
        return self


class Discover(BaseModel):
    strategy: Literal["paginate", "static", "sitemap"] = "paginate"
    url_template: str | None = None  # must contain {page}
    start_page: int = 1
    page_step: int = 1
    max_pages: int = 20
    stop_when_empty: bool = True
    urls: list[str] = Field(default_factory=list)
    sitemap_url: str | None = None
    url_pattern: str | None = None  # substring filter for sitemap entries
    # Most book sitemaps enumerate detail pages directly, so that is the default;
    # set "listing" when the sitemap points at category or index pages instead.
    sitemap_yields: Literal["detail", "listing"] = "detail"
    max_urls: int = 2000  # ceiling on detail pages taken from one sitemap

    @model_validator(mode="after")
    def _check_strategy(self) -> Discover:
        if self.strategy == "paginate":
            if not self.url_template or "{page}" not in self.url_template:
                raise ValueError("paginate strategy needs url_template containing '{page}'")
        elif self.strategy == "static" and not self.urls:
            raise ValueError("static strategy needs a non-empty urls list")
        elif self.strategy == "sitemap" and not self.sitemap_url:
            raise ValueError("sitemap strategy needs sitemap_url")
        return self


class Listing(BaseModel):
    item_selector: str | None = None
    link_selector: str | None = None  # shortcut when items are bare <a> links
    fields: dict[str, FieldSpec] = Field(default_factory=dict)


class Detail(BaseModel):
    enabled: bool = True
    # Most publisher and retail pages emit schema.org Book markup. Trying it first
    # means a recipe often needs no selectors at all.
    prefer_jsonld: bool = True
    fields: dict[str, FieldSpec] = Field(default_factory=dict)


class Recipe(BaseModel):
    id: str
    name: str
    kind: str = "publisher"
    homepage: str | None = None
    trust: float = 0.5
    enabled: bool = True
    # Set false for a site whose terms forbid automated access; the loader keeps
    # the file so the decision stays documented instead of silently dropped.
    permitted: bool = True
    permitted_note: str | None = None
    politeness: Politeness = Field(default_factory=Politeness)
    discover: Discover
    # Optional: a sitemap of detail pages needs no listing step at all.
    listing: Listing = Field(default_factory=Listing)
    detail: Detail = Field(default_factory=Detail)
    constants: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _need_a_way_to_find_books(self) -> Recipe:
        direct = self.discover.strategy == "sitemap" and self.discover.sitemap_yields == "detail"
        if not direct and not (self.listing.item_selector or self.listing.link_selector):
            raise ValueError(
                "listing needs item_selector or link_selector "
                "unless discover is a sitemap yielding detail pages"
            )
        return self

    @classmethod
    def from_yaml(cls, path: Path) -> Recipe:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return cls.model_validate(data)


def load_recipe(path: Path) -> Recipe:
    return Recipe.from_yaml(path)


def load_recipes(directory: Path) -> list[Recipe]:
    """Load every recipe in a directory, skipping underscore-prefixed templates."""
    recipes: list[Recipe] = []
    for path in sorted(Path(directory).glob("*.yaml")):
        if path.stem.startswith("_"):
            continue
        recipes.append(load_recipe(path))
    return recipes
