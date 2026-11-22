"""Cleaning text from the books before it is stored (spec §3.4): a fixture for every format."""

import pytest

from app.books.clean import clean, email_domain, extract_refs, normalize

# (raw, what must not survive, why) - every one is masked
PERSONAL = [
    ("ת.ז 123456789", "123456789", "an ID number"),
    ("ת.ז 12-345678-9", "345678", "an ID number with hyphens"),
    ("ת.ז 012 345 678", "345", "an ID number in groups of three"),
    ("ת‏.ז‎ 123456789", "123456789", "an ID number between direction marks"),
    ("ת.ז 123 456 789", "456", "an ID number joined by non-breaking spaces"),
    ("ת.ז 123–456–789", "456", "an ID number joined by en dashes"),
    ("טלפון 050-123-4567", "4567", "a mobile phone"),
    ("טל 03-1234567", "1234567", "a landline"),
    ("כרטיס 4580 1234 5678 9012", "9012", "a card number in fours"),
    ("בנק 12-345-123456", "123456", "a bank account"),
    ("שלח ל israel.israeli@example.co.il", "israel.israeli", "an email address"),
    ("INV123456", "123456", "six digits glued to letters"),
]


@pytest.mark.parametrize("raw,secret,why", PERSONAL, ids=[p[2] for p in PERSONAL])
def test_personal_numbers_and_emails_are_masked(raw, secret, why):
    cleaned, refs, masked = clean(raw)
    assert secret not in cleaned and masked >= 1, why
    assert not any(secret in r for r in refs)


@pytest.mark.parametrize("raw", [
    "16.08.2026 סך 1,170.00 ש\"ח",  # a date and an amount: dots and commas do not join a group
    "מע\"מ 18% על 550.85",
    "תשלום 3 מתוך 12",
    "עח: 50347",  # a lone document number: five digits
    "קורס Python 2026",
])
def test_dates_amounts_and_lone_document_numbers_survive(raw):
    cleaned, _, masked = clean(raw)
    assert cleaned == raw and masked == 0


def test_invisible_format_characters_are_stripped_and_text_normalized():
    assert normalize("‏שלום‎ ﻿עולם⁦!⁩") == "שלום עולם!"
    assert normalize("ﬁ x") == "fi x"  # NFKC: a ligature and a non-breaking space


@pytest.mark.parametrize("raw,refs", [
    ("עח: 50347", ["50347"]),
    ("עח: 50887-50891", ["50887-50891"]),
    ("עח 50887 - 50891 ו-50900", ["50887-50891", "50900"]),
    ("50347 50348", ["50347", "50348"]),
    ("50347, 50348", ["50347", "50348"]),
    ("חשבוניות 50347 50348-50350", ["50347", "50348-50350"]),
    ("50887–50891", ["50887-50891"]),  # an en dash makes a range too
    ("12345-67890", []),  # too wide for one series: a phone in two halves, not a range
    ("50891-50887", []),  # backwards: not a range
    ("בנק 12-345-12345", []),  # a five-digit piece of a longer number is never a reference
    ("050-12345", []),
    ("ת.ז 123456789", []),
    (None, []),
])
def test_references_are_read_before_masking_and_never_from_a_longer_number(raw, refs):
    assert extract_refs(raw) == refs


def test_a_range_is_kept_in_the_text_and_as_a_reference():
    cleaned, refs, masked = clean("עח: 50887-50891")
    assert cleaned == "עח: 50887-50891" and refs == ["50887-50891"] and masked == 0


def test_email_domain_keeps_the_domain_only():
    assert email_domain("Israel.Israeli@Example.CO.IL") == "example.co.il"
    assert email_domain("no at sign") is None and email_domain(None) is None and email_domain("x@y") is None


def test_clean_of_nothing():
    assert clean(None) == (None, [], 0)
    assert clean("   ") == (None, [], 0)


# ---- the review of 22.11: formats that slipped past, and references that were lost -------------------

BYPASSES = [
    ("טל 050.123.4567", "a phone with dots"),
    ("ת.ז 123.456.789", "an ID with dots"),
    ("ת.ז 123\u05be456\u05be789", "an ID with the Hebrew maqaf"),
    ("טל 050\u05be123\u05be4567", "a phone with the Hebrew maqaf"),
    ("ת.ז 123\u00ad456\u00ad789", "an ID split by soft hyphens (invisible)"),
    ("ת.ז 123\u061c456\u061c789", "an ID split by Arabic letter marks (invisible)"),
    ("ת.ז 123\u2028456\u2028789", "an ID split by line separators"),
    ("ת.ז 1234\n56789", "an ID across a newline"),
    ("כרטיס 4580/1234/5678/9012", "a card with slashes"),
    ("טל 05012 34567", "a phone in two five-digit halves"),
    ("ת.ז 51234 5678", "an ID as five and four digits"),
    ("16.08.2026 050-1234567", "a phone after a date"),
    ("1,234,567.89", "a number past a million"),
]


@pytest.mark.parametrize("raw,why", BYPASSES, ids=[b[1] for b in BYPASSES])
def test_no_format_of_a_personal_number_slips_past(raw, why):
    cleaned, refs, masked = clean(raw)
    assert masked >= 1 and refs == [], why
    digits = "".join(ch for ch in cleaned if ch.isdigit())
    assert len(digits) < 6 or digits == "16082026", (why, cleaned)


@pytest.mark.parametrize("raw,text,refs", [
    ("עח 50347 16.08.2026", "עח 50347 16.08.2026", ["50347"]),
    ("עח 50347 1/3", "עח 50347 1/3", ["50347"]),
    ("זיכוי ל-50887 2 תשלומים", "זיכוי ל-50887 2 תשלומים", ["50887"]),
    ("סך 1,170.00 עח 50347", "סך 1,170.00 עח 50347", ["50347"]),
])
def test_a_reference_next_to_a_date_or_a_small_number_is_kept(raw, text, refs):
    cleaned, got, masked = clean(raw)
    assert (cleaned, got, masked) == (text, refs, 0)


def test_control_characters_and_cut_emoji_never_reach_the_database():
    assert clean("a\x00b\x07c") == ("a b c", [], 0)
    assert clean("emoji \ud83d cut")[0] == "emoji cut"
