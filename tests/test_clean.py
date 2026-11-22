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


# ---- the second review of 22.11: a number is judged whole, and every digit and every joiner counts ---------

ID, PHONE = "123456789", "0501234567"
JOINERS = [" ", " - ", " / ", ", ", "-", "/", ".", ":", "|", "'", "+", " · ", "־", "―", "٬"]


def _groupings(digits):
    for i in range(1, len(digits)):
        yield [digits[:i], digits[i:]]
        for j in range(i + 1, len(digits)):
            yield [digits[:i], digits[i:j], digits[j:]]


@pytest.mark.parametrize("number", [ID, PHONE])
def test_no_grouping_of_an_id_or_a_phone_keeps_a_digit(number):
    """The review's fuzz kept 124 of 1,296 writings whole (12345 - 6789, 123 45678 9, 05012 - 34567)."""
    tried = 0
    for groups in _groupings(number):
        for joiner in JOINERS:
            cleaned, refs, masked = clean("ת.ז " + joiner.join(groups))
            tried += 1
            assert masked >= 1 and refs == [] and not any(ch.isdigit() for ch in cleaned), (groups, joiner, cleaned)
    assert tried > 500


@pytest.mark.parametrize("raw", [
    "ת.ז 123͏456͏789",  # combining grapheme joiner
    "ת.ז 1234︀5678︀9",  # variation selector 1
    "ת.ז 123️456️789",  # variation selector 16
    "ת.ז 123ㅤ456ㅤ789",  # Hangul filler: a blank letter
    "ת.ז 123⠀456⠀789",  # braille blank
    "ת.ז 123ְ456ְ789",  # a Hebrew point
    "".join(d + "️⃣" for d in PHONE),  # keycap digits, pasted from WhatsApp
    "❶❷❸❹❺❻❼❽❾",  # dingbat circled digits
    "፩፪፫፬፭፮፯፰፱",  # Ethiopic digits
    "ת.ז ١٢٣٬٤٥٦٬٧٨٩",  # Arabic-Indic, Arabic thousands separator
], ids=["cgj", "vs1", "vs16", "hangul-filler", "braille-blank", "hebrew-point", "keycaps", "dingbats", "ethiopic",
        "arabic-indic"])
def test_digits_of_any_script_and_any_invisible_joiner_are_masked(raw):
    cleaned, refs, masked = clean(raw)
    assert masked >= 1 and refs == [] and not any(ch.isdigit() for ch in (cleaned or "")), cleaned


@pytest.mark.parametrize("raw", [
    "שלח ל israel.israeli@example.\nco.il",
    "israel.israeli @example.co.il",
    "israel.israeli@ example.co.il",
    "israel.israeli@gmail",
    "israel.israeli(at)gmail.com",
    "israel.israeli[AT]gmail.com",
])
def test_an_email_in_any_spacing_loses_its_address(raw):
    cleaned, _, masked = clean(raw)
    assert masked >= 1 and "israeli" not in cleaned


@pytest.mark.parametrize("raw,refs", [
    ("חשבוניות 50887,50888", ["50887", "50888"]),
    ("חשבוניות 50887/50888", ["50887", "50888"]),
    ("חשבוניות 50887-891", ["50887-50891"]),
    ("עח 50887 - 50891", ["50887-50891"]),
    ("עח 50347 1,170.00", ["50347"]),
    ("51234 56789", ["51234", "56789"]),  # two numbers: an Israeli ten-digit phone starts with 0, never a document
    ("תשלום עבור 08/2026", []),
    ("מחזור 2026-08", []),
    ("תקופה 16.08.2026-20.08.2026", []),
    ("שנה 2025/2026", []),
    ("שנה 2025-26", []),
    ("16/8/26", []),
])
def test_lists_short_ranges_months_date_ranges_and_year_spans_are_kept(raw, refs):
    """The review's false positives: each was masked whole, and its references lost."""
    assert clean(raw) == (raw, refs, 0)


@pytest.mark.parametrize("raw", ["חשבונית 50347 2026", "עח 50347 1170", "עח 50347 67.89", "עח 50347 16/8/26"])
def test_a_document_number_beside_what_could_end_an_id_is_masked_with_it(raw):
    """The chosen cost (22.11): an ID is also written 51234 5678, 12345 67.89 or 12345 6.7.89, so a document
    number beside a bare four digits, a small amount or a short date is masked with them."""
    cleaned, refs, masked = clean(raw)
    assert masked == 1 and refs == [] and cleaned.endswith("#")


def test_stats_count_what_masking_cost():
    stats = {}
    clean("עח 50347 2026 ות.ז 123456789", stats)
    assert stats == {"numbers_masked": 2, "refs_lost": {"plain": 1}}


def test_a_field_is_bounded_and_cut_only_at_a_letter():
    from app.books.clean import MAX_KEPT, MAX_RAW

    stats = {}
    assert clean("א" + " 1" * MAX_RAW, stats) == ("א", [], 0)  # cut back to the letter: no number cut in two
    cleaned, _, _ = clean("שלום " * 30_000, stats)
    assert len(cleaned) <= MAX_KEPT and stats["fields_cut"] == 2


@pytest.mark.parametrize("raw", ["_" * 50_000, "a" * 25_000 + "@" + "a" * 25_000, "a@" * 25_000, "@" * 50_000,
                                 "1 " * 25_000, "1." * 25_000, "12345 " * 8_000])
def test_cleaning_is_linear(raw):
    """The email regex took 14 s on 32k letters around an at sign (review 22.11)."""
    import time

    started = time.perf_counter()
    clean(raw)
    assert time.perf_counter() - started < 1.0


def test_every_digit_becomes_ascii():
    assert normalize("٣٤٥ ７ ❶") == "345 7 1"
