"""Turning a page into fields.

Two strategies, tried in order. JSON-LD first: schema.org `Book` markup is
structured, stable, and already published by most publisher and retail sites, so
it needs no site-specific configuration. CSS selectors second, for the sites that
render books as undifferentiated HTML.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urljoin

from selectolax.parser import HTMLParser, Node

from orynx.logging import get_logger
from orynx.sources.html.recipe import FieldSpec
from orynx.textutil import clean_text, normalize_isbn

log = get_logger(__name__)

BOOK_TYPES = {"book", "audiobook", "product", "creativework"}


def parse_jsonld(html: str) -> list[dict[str, Any]]:
    """Return every JSON-LD object on the page, flattening @graph containers."""
    tree = HTMLParser(html)
    objects: list[dict[str, Any]] = []
    for node in tree.css('script[type="application/ld+json"]'):
        raw = node.text()
        if not raw or not raw.strip():
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            # Trailing commas and unescaped newlines are common in hand-rolled
            # markup; one salvage attempt, then move on.
            try:
                data = json.loads(re.sub(r",\s*([}\]])", r"\1", raw))
            except json.JSONDecodeError:
                continue
        objects.extend(_flatten(data))
    return objects


def _flatten(data: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if isinstance(data, list):
        for item in data:
            out.extend(_flatten(item))
    elif isinstance(data, dict):
        if "@graph" in data:
            out.extend(_flatten(data["@graph"]))
        out.append(data)
    return out


def _types_of(obj: dict[str, Any]) -> set[str]:
    raw = obj.get("@type") or obj.get("type") or []
    values = raw if isinstance(raw, list) else [raw]
    return {str(v).lower() for v in values}


def find_book_jsonld(html: str) -> dict[str, Any] | None:
    """Pick the JSON-LD object most likely to describe the book on this page."""
    candidates = parse_jsonld(html)
    for obj in candidates:
        if "book" in _types_of(obj):
            return obj
    for obj in candidates:
        types = _types_of(obj)
        if types & BOOK_TYPES and (obj.get("isbn") or obj.get("author")):
            return obj
    return None


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _name_of(value: Any) -> str | None:
    """schema.org authors appear as strings, objects, or lists of either."""
    if isinstance(value, str):
        return clean_text(value)
    if isinstance(value, dict):
        return clean_text(value.get("name") or value.get("@id"))
    return None


def book_fields_from_jsonld(obj: dict[str, Any]) -> dict[str, Any]:
    """Map schema.org Book properties onto our RawBook field names."""
    authors: list[dict[str, Any]] = []
    for entry in _as_list(obj.get("author")) + _as_list(obj.get("creator")):
        name = _name_of(entry)
        if not name:
            continue
        record: dict[str, Any] = {"name": name}
        if isinstance(entry, dict):
            record["url"] = entry.get("url") or entry.get("sameAs")
            record["bio"] = clean_text(entry.get("description"))
        authors.append(record)

    publisher = obj.get("publisher")
    offers = _as_list(obj.get("offers"))
    price = None
    if offers and isinstance(offers[0], dict):
        price = offers[0].get("price")

    rating = obj.get("aggregateRating") or {}
    image = obj.get("image")
    if isinstance(image, list):
        image = image[0] if image else None
    if isinstance(image, dict):
        image = image.get("url")

    work_example = _as_list(obj.get("workExample"))
    isbn = obj.get("isbn") or obj.get("gtin13")
    if not isbn and work_example and isinstance(work_example[0], dict):
        isbn = work_example[0].get("isbn")

    return {
        "title": clean_text(obj.get("name") or obj.get("headline")),
        "authors": authors,
        "isbn": normalize_isbn(isbn if isinstance(isbn, str) else None),
        "publisher": _name_of(publisher),
        "published_date": obj.get("datePublished") or obj.get("copyrightYear"),
        "description": clean_text(obj.get("description") or obj.get("abstract")),
        "language": obj.get("inLanguage") if isinstance(obj.get("inLanguage"), str) else None,
        "page_count": _to_int(obj.get("numberOfPages")),
        "cover_url": image if isinstance(image, str) else None,
        "categories": [c for c in _as_list(obj.get("genre")) if isinstance(c, str)],
        "average_rating": _to_float(rating.get("ratingValue")) if rating else None,
        "ratings_count": _to_int(rating.get("ratingCount") or rating.get("reviewCount"))
        if rating
        else None,
        "price": str(price) if price is not None else None,
        "url": obj.get("url") if isinstance(obj.get("url"), str) else None,
    }


def _to_int(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _to_float(value: Any) -> float | None:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _dotted(obj: Any, path: str) -> Any:
    current = obj
    for part in path.split("."):
        if isinstance(current, list):
            current = current[0] if current else None
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


TRANSFORMS = {
    "strip": lambda v: v.strip() if isinstance(v, str) else v,
    "lower": lambda v: v.lower() if isinstance(v, str) else v,
    "upper": lambda v: v.upper() if isinstance(v, str) else v,
    "title": lambda v: v.title() if isinstance(v, str) else v,
    "int": _to_int,
    "float": _to_float,
    "isbn": lambda v: normalize_isbn(v) if isinstance(v, str) else None,
    "clean": lambda v: clean_text(v) if isinstance(v, str) else v,
}


def _apply_transforms(value: Any, names: list[str]) -> Any:
    for name in names:
        func = TRANSFORMS.get(name)
        if func is None:
            log.warning("unknown transform %r; skipping", name)
            continue
        if isinstance(value, list):
            value = [func(v) for v in value]
        else:
            value = func(value)
    return value


def _node_value(node: Node, spec: FieldSpec, base_url: str) -> Any:
    if spec.attr:
        value = node.attributes.get(spec.attr)
        # Relative hrefs are useless downstream; resolve against the page.
        if value and spec.attr in {"href", "src", "data-src", "content"}:
            value = urljoin(base_url, value)
        return value
    return clean_text(node.text(separator=" ", strip=True))


def extract_field(
    tree: HTMLParser | Node,
    spec: FieldSpec,
    base_url: str,
    jsonld: dict[str, Any] | None = None,
) -> Any:
    """Resolve one FieldSpec against a page or a listing item."""
    if spec.const is not None:
        return spec.const

    value: Any = None
    if spec.jsonld and jsonld is not None:
        value = _dotted(jsonld, spec.jsonld)

    if value is None and spec.css:
        nodes = tree.css(spec.css)
        if nodes:
            if spec.many:
                value = [_node_value(n, spec, base_url) for n in nodes]
                value = [v for v in value if v]
            elif spec.index is not None:
                value = (
                    _node_value(nodes[spec.index], spec, base_url)
                    if -len(nodes) <= spec.index < len(nodes)
                    else None
                )
            else:
                value = _node_value(nodes[0], spec, base_url)

    if spec.regex and value is not None:
        pattern = re.compile(spec.regex)
        if isinstance(value, list):
            matched = [pattern.search(str(v)) for v in value]
            value = [m.group(1) if m.groups() else m.group(0) for m in matched if m]
        else:
            match = pattern.search(str(value))
            value = (match.group(1) if match.groups() else match.group(0)) if match else None

    if spec.transform:
        value = _apply_transforms(value, spec.transform)

    if spec.join and isinstance(value, list):
        value = spec.join.join(str(v) for v in value if v)

    if value in (None, "", []):
        return spec.default
    return value
