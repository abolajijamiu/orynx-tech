"""The platform registry: the sites we harvest authors from.

A platform is not itself the lead. It is context attached to every author found
through it, and the most useful context it carries is *what the author already
paid for*. An author on a vanity imprint has bought publishing. An author on a
paid-review site has bought marketing. That purchase history is a far stronger
qualifier than anything in a book's metadata, so it drives both scoring and the
suggested pitch.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator

PACKAGE_ROOT = Path(__file__).resolve().parent
REGISTRY_PATH = PACKAGE_ROOT / "registry.yaml"

Category = Literal[
    "review_paid",       # author pays for the review
    "review_trade",      # editorial review, publisher-submitted
    "review_editorial",  # literary magazines and critical outlets
    "publisher_vanity",  # author pays to be published
    "publisher_hybrid",  # author co-invests
    "publisher_indie",   # small press, publisher pays
    "selfpub_platform",  # DIY tooling and distribution
    "distributor",
    "promo_service",     # newsletters, ads, launch promotion
    "arc_service",       # advance review copy distribution
    "pr_agency",
    "trade_news",
    "education",         # courses, blogs, associations
    "crowdfunding",
]

# What being listed on this platform says about the author's spending. This is
# the field that makes a lead qualifiable, so every platform must declare one.
AuthorSignal = Literal[
    "vanity_published",       # paid, often heavily, to publish
    "hybrid_published",       # co-invested with a press
    "indie_published",        # a small press took them on
    "self_published",         # DIY, budget unknown
    "paid_review",            # bought a review
    "paid_promotion",         # bought marketing
    "trade_published",        # a publisher paid them; weakest services lead
    "seeking_publication",    # actively submitting
    "community_listed",       # a listing only; almost no signal
]

# How likely we are to get book records off the site at all.
Extractability = Literal["likely", "paywalled", "login_required", "js_rendered", "unknown"]


class Platform(BaseModel):
    id: str
    name: str
    homepage: str
    category: Category
    author_signal: AuthorSignal
    country: str = "US"
    # Several brands often share one owner; outreach must not hit the same desk
    # once per brand, and the registry is where that is recorded.
    owner: str | None = None
    services: list[str] = Field(default_factory=list)
    contact_page: str | None = None
    extractability: Extractability = "unknown"
    # 0..1 multiplier applied to leads found here.
    weight: float = 0.5
    enabled: bool = True
    notes: str | None = None

    @model_validator(mode="after")
    def _check_weight(self) -> Platform:
        if not 0.0 <= self.weight <= 1.0:
            raise ValueError(f"{self.id}: weight must be between 0 and 1")
        return self


class Registry(BaseModel):
    platforms: list[Platform]

    @classmethod
    def load(cls, path: Path | None = None) -> Registry:
        data = yaml.safe_load(Path(path or REGISTRY_PATH).read_text(encoding="utf-8"))
        return cls.model_validate(data)

    def by_id(self, platform_id: str) -> Platform | None:
        return next((p for p in self.platforms if p.id == platform_id), None)

    def enabled(self) -> list[Platform]:
        return [p for p in self.platforms if p.enabled]

    def by_owner(self) -> dict[str, list[Platform]]:
        """Group brands under a shared owner so outreach can be deduplicated."""
        groups: dict[str, list[Platform]] = {}
        for platform in self.platforms:
            key = platform.owner or platform.name
            groups.setdefault(key, []).append(platform)
        return groups

    def extractable(self) -> list[Platform]:
        return [
            p for p in self.enabled()
            if p.extractability in {"likely", "js_rendered", "unknown"}
        ]
