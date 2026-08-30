"""Shared text, ISBN and date helpers used by both adapters and the pipeline."""

from __future__ import annotations

import re
import unicodedata
from datetime import date

from dateutil import parser as date_parser

_WHITESPACE = re.compile(r"\s+")
_NON_ALNUM = re.compile(r"[^a-z0-9 ]+")
_HTML_TAG = re.compile(r"<[^>]+>")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# Tags become spaces so words never fuse, which then strands a space before
# punctuation ("home <b>x</b>." -> "home x ."). These put it back.
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([.,;:!?%)\]}])")
_SPACE_AFTER_OPEN = re.compile(r"([(\[{])\s+")

# Titles are compared after stripping leading articles, so "The Silent Wife" and
# "Silent Wife" collapse to the same dedupe key.
_LEADING_ARTICLES = ("the ", "a ", "an ")

_ROLE_SUFFIXES = re.compile(
    r",?\s*\b(phd|ph\.d|md|m\.d|jr|sr|ii|iii|iv|esq|mba|dds)\b\.?$", re.IGNORECASE
)


def clean_text(value: str | None) -> str | None:
    """Strip tags and collapse whitespace. Returns None for empty results."""
    if not value:
        return None
    text = _HTML_TAG.sub(" ", value)
    text = text.replace("&amp;", "&").replace("&#39;", "'").replace("&quot;", '"')
    text = text.replace("&nbsp;", " ").replace("&lt;", "<").replace("&gt;", ">")
    text = _WHITESPACE.sub(" ", text).strip()
    text = _SPACE_BEFORE_PUNCT.sub(r"\1", text)
    text = _SPACE_AFTER_OPEN.sub(r"\1", text)
    return text or None


def slugify(value: str) -> str:
    text = unicodedata.normalize("NFKD", value)
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    text = _NON_ALNUM.sub(" ", text)
    return _WHITESPACE.sub("-", text.strip())


def normalize_title(title: str) -> str:
    """Fold a title to a comparable form for blocking and dedupe."""
    text = unicodedata.normalize("NFKD", title)
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    text = _NON_ALNUM.sub(" ", text)
    text = _WHITESPACE.sub(" ", text).strip()
    for article in _LEADING_ARTICLES:
        if text.startswith(article):
            text = text[len(article) :]
            break
    return text


def normalize_person(name: str) -> str:
    """Fold a personal name: strip accents, honorifics, punctuation and suffixes.

    Handles the two orderings platforms use ("King, Stephen" and "Stephen King")
    by rewriting the comma form into given-family order.
    """
    text = unicodedata.normalize("NFKD", name)
    text = "".join(c for c in text if not unicodedata.combining(c)).strip()
    text = _ROLE_SUFFIXES.sub("", text)
    if "," in text:
        family, _, given = text.partition(",")
        text = f"{given.strip()} {family.strip()}"
    text = text.lower()
    text = re.sub(r"\b(dr|mr|mrs|ms|prof|professor|sir|dame)\b\.?", " ", text)
    text = _NON_ALNUM.sub(" ", text)
    return _WHITESPACE.sub(" ", text).strip()


def person_block_key(name: str) -> str:
    """Blocking key for candidate generation: family name + first initial.

    Fuzzy-matching every author against every other is quadratic; comparing only
    within a block keeps dedupe linear in practice.
    """
    normalized = normalize_person(name)
    if not normalized:
        return ""
    parts = normalized.split()
    if len(parts) == 1:
        return parts[0]
    return f"{parts[-1]}:{parts[0][0]}"


def normalize_isbn(value: str | None) -> str | None:
    if not value:
        return None
    digits = re.sub(r"[^0-9Xx]", "", value).upper()
    if len(digits) == 13 and digits.isdigit():
        return digits
    if len(digits) == 10:
        return digits
    return None


def isbn10_to_13(isbn10: str) -> str | None:
    if len(isbn10) != 10:
        return None
    core = "978" + isbn10[:9]
    total = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(core))
    check = (10 - total % 10) % 10
    return core + str(check)


def split_isbns(values: list[str] | None) -> tuple[str | None, str | None]:
    """Return (isbn13, isbn10) from a mixed list, deriving the 13 when absent."""
    isbn13 = isbn10 = None
    for candidate in values or []:
        normalized = normalize_isbn(candidate)
        if not normalized:
            continue
        if len(normalized) == 13 and not isbn13:
            isbn13 = normalized
        elif len(normalized) == 10 and not isbn10:
            isbn10 = normalized
    if not isbn13 and isbn10:
        isbn13 = isbn10_to_13(isbn10)
    return isbn13, isbn10


def parse_date(value: str | None) -> tuple[date | None, int | None]:
    """Parse the many partial date formats platforms publish.

    Returns (date, year); a bare year yields (None, year) rather than inventing
    a January 1st that would distort recency scoring.
    """
    if not value:
        return None, None
    text = str(value).strip()
    if not text:
        return None, None
    if re.fullmatch(r"\d{4}", text):
        return None, int(text)
    match = re.search(r"(1[0-9]{3}|20[0-9]{2})", text)
    year = int(match.group(1)) if match else None
    try:
        parsed = date_parser.parse(text, default=None, fuzzy=True)
        return parsed.date(), parsed.year
    except (ValueError, OverflowError, TypeError):
        return None, year


def extract_emails(text: str | None) -> list[str]:
    if not text:
        return []
    seen: list[str] = []
    for match in _EMAIL.findall(text):
        lowered = match.lower()
        if lowered not in seen:
            seen.append(lowered)
    return seen
