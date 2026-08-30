"""Discovery of every available source, code-based or recipe-based.

Callers ask for sources by id and never care which kind they get; that is what
lets the pipeline treat a JSON API and a scraped catalogue identically.
"""

from __future__ import annotations

from pathlib import Path

from orynx.config import get_settings
from orynx.fetch import PoliteClient
from orynx.logging import get_logger
from orynx.sources.api.crossref import CrossrefSource
from orynx.sources.api.googlebooks import GoogleBooksSource
from orynx.sources.api.openalex import OpenAlexSource
from orynx.sources.api.openlibrary import OpenLibrarySource
from orynx.sources.base import BaseSource, SourceMeta
from orynx.sources.html.generic import RecipeSource
from orynx.sources.html.recipe import Recipe, load_recipes

log = get_logger(__name__)

API_SOURCES: dict[str, type[BaseSource]] = {
    OpenLibrarySource.meta.id: OpenLibrarySource,
    GoogleBooksSource.meta.id: GoogleBooksSource,
    CrossrefSource.meta.id: CrossrefSource,
    OpenAlexSource.meta.id: OpenAlexSource,
}


class SourceRegistry:
    def __init__(self, recipe_dir: Path | None = None) -> None:
        self.recipe_dir = Path(recipe_dir or get_settings().recipe_dir)
        self._recipes: dict[str, Recipe] | None = None

    @property
    def recipes(self) -> dict[str, Recipe]:
        if self._recipes is None:
            self._recipes = {}
            if self.recipe_dir.exists():
                for recipe in load_recipes(self.recipe_dir):
                    if recipe.id in API_SOURCES:
                        log.warning(
                            "recipe %s shadows a built-in adapter; the adapter wins",
                            recipe.id,
                        )
                        continue
                    self._recipes[recipe.id] = recipe
        return self._recipes

    def ids(self, *, enabled_only: bool = True) -> list[str]:
        ids = list(API_SOURCES)
        ids += [r.id for r in self.recipes.values() if r.enabled or not enabled_only]
        return sorted(set(ids))

    def describe(self) -> list[SourceMeta]:
        metas = [cls.meta for cls in API_SOURCES.values()]
        for recipe in self.recipes.values():
            metas.append(
                SourceMeta(
                    id=recipe.id,
                    name=recipe.name,
                    kind=recipe.kind,
                    homepage=recipe.homepage,
                    trust=recipe.trust,
                    notes=recipe.permitted_note,
                )
            )
        return sorted(metas, key=lambda m: m.id)

    def build(self, source_id: str, client: PoliteClient) -> BaseSource:
        if source_id in API_SOURCES:
            return API_SOURCES[source_id](client)
        recipe = self.recipes.get(source_id)
        if recipe is None:
            known = ", ".join(self.ids()) or "none"
            raise KeyError(f"unknown source {source_id!r}. Known sources: {known}")
        return RecipeSource(client, recipe)

    def build_many(self, source_ids: list[str], client: PoliteClient) -> list[BaseSource]:
        return [self.build(sid, client) for sid in source_ids]


_registry: SourceRegistry | None = None


def get_registry() -> SourceRegistry:
    global _registry
    if _registry is None:
        _registry = SourceRegistry()
    return _registry
