from datetime import date

import pytest

from orynx.textutil import (
    clean_text,
    extract_emails,
    isbn10_to_13,
    normalize_isbn,
    normalize_person,
    normalize_title,
    parse_date,
    person_block_key,
    split_isbns,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("King, Stephen", "stephen king"),
        ("Dr. Stephen King PhD", "stephen king"),
        ("  STEPHEN   KING ", "stephen king"),
        ("Gabriel García Márquez", "gabriel garcia marquez"),
        ("O'Brien, Flann", "flann o brien"),
    ],
)
def test_normalize_person_folds_variants(raw, expected):
    assert normalize_person(raw) == expected


def test_person_block_key_groups_orderings():
    assert person_block_key("Stephen King") == person_block_key("King, Stephen")


def test_normalize_title_drops_leading_article_and_punctuation():
    assert normalize_title("The Silent Wife") == normalize_title("Silent Wife!")


def test_normalize_title_keeps_internal_articles():
    assert normalize_title("A Tale of the Sea") == "tale of the sea"


def test_isbn10_to_13_uses_correct_checksum():
    assert isbn10_to_13("0306406152") == "9780306406157"


def test_normalize_isbn_strips_formatting():
    assert normalize_isbn("978-0-306-40615-7") == "9780306406157"
    assert normalize_isbn("nonsense") is None


def test_split_isbns_derives_13_from_10():
    assert split_isbns(["0306406152"]) == ("9780306406157", "0306406152")


def test_split_isbns_prefers_supplied_13():
    isbn13, isbn10 = split_isbns(["0306406152", "9781234567897"])
    assert isbn13 == "9781234567897"
    assert isbn10 == "0306406152"


def test_parse_date_bare_year_does_not_invent_a_day():
    assert parse_date("2024") == (None, 2024)


def test_parse_date_full_date():
    assert parse_date("March 3, 2024") == (date(2024, 3, 3), 2024)


def test_parse_date_handles_junk():
    assert parse_date("forthcoming") == (None, None)
    assert parse_date(None) == (None, None)


def test_clean_text_strips_markup_and_entities():
    assert clean_text("<b>Hello</b>&nbsp;&amp; welcome  ") == "Hello & welcome"
    assert clean_text("   ") is None


def test_extract_emails_deduplicates_case_insensitively():
    assert extract_emails("A@x.com and a@X.com") == ["a@x.com"]
