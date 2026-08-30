"""Lead scoring.

Every signal returns 0..1 and is combined with a weight, so a score is always
explainable: `Lead.reasons` records which signals fired and how strongly. Weights
live in named profiles because "a good lead" means something different when you
sell editing than when you sell rights.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from orynx.db.models import Author, Book

# Imprints of the large trade houses. An author here already has a publisher's
# editorial, design and publicity teams, so they are a weak services lead.
TRADE_IMPRINTS = {
    "penguin", "random house", "knopf", "doubleday", "viking", "riverhead",
    "hachette", "little, brown", "grand central", "orbit", "harpercollins",
    "harper", "william morrow", "avon", "simon & schuster", "scribner",
    "atria", "gallery books", "macmillan", "farrar, straus", "st. martin",
    "tor", "henry holt", "picador", "bloomsbury", "faber", "vintage",
    "pan macmillan", "hodder", "headline", "transworld", "canongate",
}

# Publishers whose authors pay for production and therefore buy adjacent
# services. Membership is a positive signal, not a judgement about the publisher.
AUTHOR_FUNDED_MARKERS = {
    "independently published", "self-published", "createspace", "kdp",
    "kindle direct", "lulu", "xlibris", "authorhouse", "iuniverse",
    "archway", "balboa", "dorrance", "page publishing", "austin macauley",
    "olympia publishers", "pegasus publishers", "koehler", "morgan james",
    "greenleaf", "she writes press", "matador", "troubador", "book guild",
}


@dataclass(slots=True)
class ScoringProfile:
    """Named weighting. Weights need not sum to 1; scores are normalised."""

    name: str
    weights: dict[str, float] = field(default_factory=dict)
    # A book older than this contributes nothing to the recency signal.
    recency_horizon_months: int = 36
    low_visibility_threshold: int = 50


PROFILES: dict[str, ScoringProfile] = {
    "services": ScoringProfile(
        name="services",
        weights={
            "recency": 0.28,
            "author_funded": 0.22,
            "small_catalogue": 0.16,
            "low_visibility": 0.12,
            "contactable": 0.14,
            "source_trust": 0.04,
            "corroborated": 0.04,
        },
    ),
    "marketing": ScoringProfile(
        name="marketing",
        weights={
            "recency": 0.34,
            "low_visibility": 0.24,
            "author_funded": 0.12,
            "contactable": 0.16,
            "small_catalogue": 0.06,
            "source_trust": 0.04,
            "corroborated": 0.04,
        },
        recency_horizon_months=18,
    ),
    "saas": ScoringProfile(
        name="saas",
        weights={
            "contactable": 0.30,
            "recency": 0.22,
            "author_funded": 0.18,
            "small_catalogue": 0.12,
            "low_visibility": 0.08,
            "source_trust": 0.05,
            "corroborated": 0.05,
        },
    ),
    "rights": ScoringProfile(
        name="rights",
        weights={
            # Rights scouting inverts the services logic: visibility and a real
            # publisher are what make a title worth licensing.
            "high_visibility": 0.34,
            "trade_published": 0.22,
            "recency": 0.18,
            "corroborated": 0.14,
            "source_trust": 0.07,
            "contactable": 0.05,
        },
        recency_horizon_months=60,
    ),
}

DEFAULT_PROFILE = "services"


@dataclass(slots=True)
class ScoreResult:
    score: float
    tier: str
    reasons: list[dict[str, float | str]]


def _months_since(published_on: date | None, year: int | None, today: date) -> float | None:
    if published_on:
        return (today.year - published_on.year) * 12 + (today.month - published_on.month)
    if year:
        return (today.year - year) * 12
    return None


def _publisher_signals(publisher: str | None) -> tuple[bool, bool]:
    """Return (author_funded, trade_published) for a publisher string."""
    if not publisher:
        # No publisher recorded most often means self-published or a tiny press.
        return True, False
    lowered = publisher.lower()
    trade = any(imprint in lowered for imprint in TRADE_IMPRINTS)
    funded = any(marker in lowered for marker in AUTHOR_FUNDED_MARKERS)
    return funded, trade


def score_lead(
    author: Author,
    book: Book,
    *,
    profile: ScoringProfile | str = DEFAULT_PROFILE,
    source_trust: float = 0.5,
    source_count: int = 1,
    book_count: int = 1,
    contact_count: int = 0,
    today: date | None = None,
) -> ScoreResult:
    """Score one author/book pair. Pure: no database access, easy to test."""
    if isinstance(profile, str):
        profile = PROFILES.get(profile, PROFILES[DEFAULT_PROFILE])
    today = today or date.today()

    author_funded, trade = _publisher_signals(book.publisher)
    months = _months_since(book.published_on, book.published_year, today)
    ratings = book.ratings_count or 0

    signals: dict[str, float] = {}

    if months is None:
        signals["recency"] = 0.3  # unknown date: neither fresh nor stale
    elif months < 0:
        signals["recency"] = 1.0  # forthcoming titles are the warmest leads
    else:
        horizon = profile.recency_horizon_months
        signals["recency"] = max(0.0, 1.0 - (months / horizon)) if horizon else 0.0

    signals["author_funded"] = 1.0 if author_funded else (0.0 if trade else 0.5)
    signals["trade_published"] = 1.0 if trade else 0.0
    signals["small_catalogue"] = 1.0 if book_count <= 1 else (0.6 if book_count <= 3 else 0.1)

    threshold = profile.low_visibility_threshold
    signals["low_visibility"] = max(0.0, 1.0 - (ratings / threshold)) if threshold else 0.0
    signals["high_visibility"] = min(1.0, ratings / (threshold * 10)) if threshold else 0.0

    has_site = bool(author.website)
    signals["contactable"] = min(1.0, (contact_count * 0.4) + (0.4 if has_site else 0.0))
    signals["source_trust"] = max(0.0, min(1.0, source_trust))
    signals["corroborated"] = min(1.0, (source_count - 1) / 2)

    total_weight = sum(profile.weights.values()) or 1.0
    reasons: list[dict[str, float | str]] = []
    accumulated = 0.0
    for key, weight in profile.weights.items():
        value = signals.get(key, 0.0)
        contribution = value * weight
        accumulated += contribution
        if value > 0:
            reasons.append(
                {
                    "signal": key,
                    "value": round(value, 3),
                    "weight": weight,
                    "points": round(100 * contribution / total_weight, 2),
                }
            )

    score = round(100 * accumulated / total_weight, 2)
    reasons.sort(key=lambda r: float(r["points"]), reverse=True)
    return ScoreResult(score=score, tier=tier_for(score), reasons=reasons)


def tier_for(score: float) -> str:
    if score >= 75:
        return "A"
    if score >= 55:
        return "B"
    if score >= 35:
        return "C"
    return "D"
