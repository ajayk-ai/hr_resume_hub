from datetime import datetime
from decimal import Decimal

import pytest

from hr_resume_hub import parsers
from hr_resume_hub.parsers import IST


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("July 29, 2026 at 3:01 PM IST", datetime(2026, 7, 29, 15, 1, tzinfo=IST)),
        ("September 8, 2026 at 9 PM IST", datetime(2026, 9, 8, 21, 0, tzinfo=IST)),
        ("8/31/2026", datetime(2026, 8, 31, tzinfo=IST)),  # month/day/year, midnight IST
        ("not a date", None),
        ("", None),
    ],
)
def test_timestamp(value, expected):
    assert parsers.timestamp(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("8+", Decimal("8.00")),
        ("3.3", Decimal("3.30")),
        ("2 Years", Decimal("2.00")),
        ("9+ Years", Decimal("9.00")),
        ("6 Years & 6 Months", Decimal("6.50")),
        ("3 Years 4 Months", Decimal("3.33")),
        ("8 Months", Decimal("0.67")),
        ("1/2/1900", None),  # Sheets auto-converted to a date; unrecoverable
        ("5-7", None),
        ("", None),
    ],
)
def test_experience(value, expected):
    assert parsers.experience(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [("85", 85), (" 90 ", 90), ("3.5", None), ("85\n60\n70", None), ("", None)],
)
def test_integer(value, expected):
    assert parsers.integer(value) == expected


def test_link_strips_markdown_wrapper():
    wrapped = "[https://drive.google.com/file/d/abc/view](https://drive.google.com/file/d/abc/view)"
    assert parsers.link(wrapped) == "https://drive.google.com/file/d/abc/view"
    assert parsers.link("https://example.com/x") == "https://example.com/x"
    assert parsers.link("  ") is None


def test_text_trims_and_blanks_to_none():
    assert parsers.text("  Coimbatore ") == "Coimbatore"
    assert parsers.text("   ") is None
