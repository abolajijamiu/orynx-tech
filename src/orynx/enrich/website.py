"""Reading contact details off an author's own website.

The last step in the enrichment chain, and the only one that visits a page
outside a known data source. It reads what an author has chosen to publish on
their own site. It does not guess addresses, probe mail servers, or follow links
off the page it was given.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from selectolax.parser import HTMLParser

from orynx.enrich.base import AuthorEnricher, AuthorProfile
from orynx.fetch import PoliteClient, RobotsDenied
from orynx.logging import get_logger
from orynx.textutil import extract_emails

log = get_logger(__name__)

SOCIAL_PATTERNS = {
    "twitter": re.compile(r"https?://(?:www\.)?(?:twitter|x)\.com/([A-Za-z0-9_]{2,30})"),
    "instagram": re.compile(r"https?://(?:www\.)?instagram\.com/([A-Za-z0-9_.]{2,30})"),
    "facebook": re.compile(r"https?://(?:www\.)?facebook\.com/([A-Za-z0-9_.\-]{3,60})"),
    "linkedin": re.compile(r"https?://(?:[a-z]{2,3}\.)?linkedin\.com/in/([A-Za-z0-9\-_%]{3,80})"),
    "goodreads": re.compile(r"https?://(?:www\.)?goodreads\.com/author/show/([0-9]+[^\"'\s<]*)"),
}

# Shared inboxes belong to an organisation rather than a person. On a third
# party's domain they are noise; on the author's own domain "hello@" is often
# exactly how they ask to be reached, so the domain decides, not the word.
GENERIC_LOCAL_PARTS = {
    "info", "contact", "support", "admin", "hello", "sales", "office",
    "webmaster", "noreply", "no-reply", "help", "press", "media", "enquiries",
    "subscriptions", "billing", "careers", "jobs", "abuse", "postmaster",
}


@dataclass(slots=True)
class ContactFindings:
    emails: list[str] = field(default_factory=list)
    socials: dict[str, str] = field(default_factory=dict)
    source_url: str | None = None

    @property
    def is_empty(self) -> bool:
        return not self.emails and not self.socials


def _registrable(host: str) -> str:
    """Crude eTLD+1: enough to tell "amara.example" from "somepublisher.com"."""
    parts = [p for p in host.lower().split(".") if p]
    return ".".join(parts[-2:]) if len(parts) >= 2 else host.lower()


def _keep_email(email: str, page_url: str) -> bool:
    """Decide whether an address found on a page belongs to that page's owner.

    On the site's own domain every address is theirs, including "hello@". An
    address on some other domain is only worth keeping if it looks personal
    rather than like an organisation's shared inbox.
    """
    local, _, domain = email.lower().partition("@")
    if not domain:
        return False
    # hostname rather than netloc: netloc carries the port, which would make
    # every comparison fail on a site served off :8080.
    page_domain = _registrable(urlsplit(page_url).hostname or "")
    if page_domain and _registrable(domain) == page_domain:
        return True
    return local not in GENERIC_LOCAL_PARTS


def parse_contacts(html: str, url: str) -> ContactFindings:
    """Read contact details out of a page's markup."""
    findings = ContactFindings(source_url=url)
    tree = HTMLParser(html)

    for node in tree.css('a[href^="mailto:"]'):
        href = node.attributes.get("href", "")
        address = href[7:].split("?", 1)[0].strip().lower()
        if address and "@" in address and address not in findings.emails:
            findings.emails.append(address)

    # Body text catches addresses written out rather than linked.
    body = tree.body.text(separator=" ", strip=True) if tree.body else ""
    for address in extract_emails(body):
        if address not in findings.emails:
            findings.emails.append(address)

    findings.emails = [e for e in findings.emails if _keep_email(e, url)][:5]

    for network, pattern in SOCIAL_PATTERNS.items():
        match = pattern.search(html)
        if match:
            findings.socials[network] = match.group(0)

    return findings


async def discover_contacts(client: PoliteClient, url: str) -> ContactFindings:
    """Fetch one page and extract contacts. Never raises; returns empty on failure."""
    if not url or not url.startswith(("http://", "https://")):
        return ContactFindings()
    try:
        result = await client.get(url)
    except RobotsDenied as exc:
        log.debug("contact discovery skipped: %s", exc)
        return ContactFindings()
    except Exception as exc:
        log.debug("contact discovery failed for %s: %s", url, exc)
        return ContactFindings()
    if not result.ok:
        return ContactFindings()
    return parse_contacts(result.text, result.url)


class WebsiteEnricher(AuthorEnricher):
    """Visits the author's own site, once one of the other enrichers has found it."""

    id = "website"
    name = "Author website"

    async def enrich(
        self, author_name: str, hints: AuthorProfile | None = None
    ) -> AuthorProfile:
        website = hints.website if hints else None
        if not website:
            return AuthorProfile()

        findings = await discover_contacts(self.client, website)
        if findings.is_empty:
            return AuthorProfile()

        return AuthorProfile(
            emails=findings.emails,
            socials=dict(findings.socials),
            source_id=self.id,
            source_url=findings.source_url,
            # An address published on the author's own site is about as direct as
            # open data gets, but the page could still be a publisher's landing page.
            confidence=0.8,
        )
