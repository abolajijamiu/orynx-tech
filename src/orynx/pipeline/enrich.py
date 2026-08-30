"""Optional contact discovery.

Off by default. When enabled, this visits an author page already linked from a
book record and reads publicly published contact details, recording where each
one came from. It does not guess addresses, probe mail servers, or follow links
off the page it was given.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from selectolax.parser import HTMLParser

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

# Shared inboxes belong to the site, not the author, and mailing them is spam.
GENERIC_LOCAL_PARTS = {
    "info", "contact", "support", "admin", "hello", "sales", "office",
    "webmaster", "noreply", "no-reply", "help", "press", "media", "enquiries",
}


@dataclass(slots=True)
class ContactFindings:
    emails: list[str] = field(default_factory=list)
    socials: dict[str, str] = field(default_factory=dict)
    source_url: str | None = None

    @property
    def is_empty(self) -> bool:
        return not self.emails and not self.socials


def _is_personal(email: str) -> bool:
    local = email.split("@", 1)[0].lower()
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

    findings.emails = [e for e in findings.emails if _is_personal(e)][:5]

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
