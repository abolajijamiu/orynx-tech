from selectolax.parser import HTMLParser

from orynx.sources.html.extract import (
    book_fields_from_jsonld,
    extract_field,
    find_book_jsonld,
    parse_jsonld,
)
from orynx.sources.html.recipe import FieldSpec
from tests.conftest import fixture_text


def test_finds_book_in_jsonld():
    data = find_book_jsonld(fixture_text("html", "detail_jsonld.html"))
    assert data is not None
    assert data["name"] == "The Quiet Harbour"


def test_book_fields_from_jsonld_maps_schema_org():
    fields = book_fields_from_jsonld(find_book_jsonld(fixture_text("html", "detail_jsonld.html")))
    assert fields["title"] == "The Quiet Harbour"
    assert fields["isbn"] == "9781234567897"
    assert fields["publisher"] == "Koehler Books"
    assert fields["page_count"] == 312
    assert fields["ratings_count"] == 4
    assert fields["price"] == "14.99"
    assert fields["authors"][0]["name"] == "Amara Nwosu"


def test_jsonld_graph_containers_are_flattened():
    html = """<script type="application/ld+json">
    {"@graph":[{"@type":"WebSite","name":"Site"},{"@type":"Book","name":"Inner","isbn":"123"}]}
    </script>"""
    assert find_book_jsonld(html)["name"] == "Inner"


def test_jsonld_with_trailing_comma_is_salvaged():
    html = '<script type="application/ld+json">{"@type":"Book","name":"Sloppy",}</script>'
    assert find_book_jsonld(html)["name"] == "Sloppy"


def test_malformed_jsonld_is_skipped_not_raised():
    assert parse_jsonld('<script type="application/ld+json">{ nope }</script>') == []


def test_extract_field_reads_attribute_and_resolves_relative_url():
    tree = HTMLParser('<a class="t" href="/books/x">X</a>')
    spec = FieldSpec(css="a.t", attr="href")
    assert extract_field(tree, spec, "https://press.test/list") == "https://press.test/books/x"


def test_extract_field_many_and_join():
    tree = HTMLParser('<p class="a">One</p><p class="a">Two</p>')
    assert extract_field(tree, FieldSpec(css="p.a", many=True), "https://x.test") == ["One", "Two"]
    joined = FieldSpec(css="p.a", many=True, join=", ")
    assert extract_field(tree, joined, "https://x.test") == "One, Two"


def test_extract_field_regex_capture_and_transform():
    tree = HTMLParser('<span class="i">ISBN: 978-0-306-40615-7</span>')
    spec = FieldSpec(css="span.i", regex=r"([0-9Xx\-]{10,17})", transform=["isbn"])
    assert extract_field(tree, spec, "https://x.test") == "9780306406157"


def test_extract_field_falls_back_to_default():
    tree = HTMLParser("<div></div>")
    spec = FieldSpec(css=".missing", default="unknown")
    assert extract_field(tree, spec, "https://x.test") == "unknown"


def test_extract_field_const_wins_without_lookup():
    tree = HTMLParser("<div></div>")
    assert extract_field(tree, FieldSpec(const="Fixed Press"), "https://x.test") == "Fixed Press"


def test_extract_field_reads_dotted_jsonld_path():
    spec = FieldSpec(jsonld="author.name")
    data = {"author": {"name": "Amara Nwosu"}}
    assert extract_field(HTMLParser("<div/>"), spec, "https://x.test", data) == "Amara Nwosu"
