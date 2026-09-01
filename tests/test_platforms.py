"""The platform registry: shape, coverage and the cleanup guarantees."""

from __future__ import annotations

from collections import Counter
from urllib.parse import urlsplit

import pytest
from pydantic import ValidationError

from orynx.platforms.schema import Platform, Registry

# Domains judged wrong or defunct during list cleanup. They must stay out, so a
# future edit cannot quietly reintroduce a prospect that does not exist.
REJECTED_DOMAINS = {
    "losangelesbookreview.com", "chicagobookreview.com", "publishamerica.com",
    "bookmarketingexperts.com", "bookreview247.com", "bookreviewclub.com",
    "authorsdaily.com", "bookmarketing.pro", "bookadrenaline.com",
    "bookreviewsandmore.com",
}

# Self-serve portals and reader communities: no author list to harvest.
NON_PROSPECT_DOMAINS = {
    "kdp.amazon.com", "authors.apple.com", "play.google.com",
    "press.barnesandnoble.com", "kobo.com", "goodreads.com", "librarything.com",
    "app.thestorygraph.com", "hardcover.app", "submittable.com", "kickstarter.com",
}


@pytest.fixture(scope="module")
def registry() -> Registry:
    return Registry.load()


def host_of(url: str) -> str:
    host = urlsplit(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def test_registry_loads_and_is_substantial(registry):
    assert len(registry.platforms) >= 150


def test_every_platform_has_a_unique_id(registry):
    ids = [p.id for p in registry.platforms]
    duplicates = [i for i, n in Counter(ids).items() if n > 1]
    assert not duplicates, f"duplicate ids: {duplicates}"


def test_no_two_platforms_share_a_homepage_host(registry):
    """One row per site: a repeated host means outreach would double up."""
    hosts = [host_of(p.homepage) for p in registry.platforms]
    duplicates = [h for h, n in Counter(hosts).items() if n > 1]
    # Reedsy Discovery and ALLi Advice are deliberate sub-brands on shared hosts.
    allowed = {"reedsy.com", "selfpublishingadvice.org"}
    assert set(duplicates) <= allowed, f"unexpected duplicate hosts: {duplicates}"


def test_rejected_domains_stayed_out(registry):
    hosts = {host_of(p.homepage) for p in registry.platforms}
    leaked = hosts & REJECTED_DOMAINS
    assert not leaked, f"unverified domains reintroduced: {leaked}"


def test_non_prospect_portals_stayed_out(registry):
    hosts = {host_of(p.homepage) for p in registry.platforms}
    leaked = hosts & NON_PROSPECT_DOMAINS
    assert not leaked, f"portals are not harvestable prospects: {leaked}"


def test_every_homepage_is_an_absolute_https_url(registry):
    for platform in registry.platforms:
        assert platform.homepage.startswith("https://"), platform.id


def test_shared_owners_are_recorded(registry):
    """Author Solutions runs seven of these brands; outreach must know that."""
    groups = registry.by_owner()
    assert len(groups.get("Author Solutions", [])) == 7
    assert len(groups.get("Written Word Media", [])) == 3


def test_author_funded_platforms_outrank_trade_ones(registry):
    """An author who paid to publish is a warmer lead than one who was paid."""
    vanity = [p.weight for p in registry.platforms if p.author_signal == "vanity_published"]
    trade = [p.weight for p in registry.platforms if p.author_signal == "trade_published"]
    assert min(vanity) > max(trade)


def test_paywalled_and_login_sites_are_marked(registry):
    """Sites we cannot read must be flagged, not silently yield nothing."""
    blocked = [p for p in registry.platforms if p.extractability in {"paywalled", "login_required"}]
    assert len(blocked) >= 5
    assert all(p.id for p in blocked)


def test_extractable_excludes_blocked_sites(registry):
    ids = {p.id for p in registry.extractable()}
    assert "publishersweekly" not in ids   # paywalled
    assert "netgalley" not in ids          # login required
    assert "pacificbookreview" in ids


def test_every_platform_declares_a_purchase_signal(registry):
    for platform in registry.platforms:
        assert platform.author_signal, platform.id
        assert platform.services, f"{platform.id} lists no services"


def test_weight_must_be_a_fraction():
    base = dict(id="x", name="X", homepage="https://x.test",
                category="review_paid", author_signal="paid_review")
    with pytest.raises(ValidationError, match="between 0 and 1"):
        Platform(**base, weight=1.5)


def test_lookup_by_id(registry):
    assert registry.by_id("koehlerbooks").category == "publisher_hybrid"
    assert registry.by_id("nope") is None
