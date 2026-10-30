"""Convert raw Google Sheet cell text into typed values.

Every parser takes the cell text and returns a typed value, or None when the cell is
empty or cannot be interpreted. Parsers never raise.
"""
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal

IST = timezone(timedelta(hours=5, minutes=30))

_TWO_PLACES = Decimal("0.01")
_NUMBER = re.compile(r"\s*(\d+(?:\.\d+)?)\s*\+?\s*")
_EXPERIENCE = re.compile(
    r"\s*(\d+(?:\.\d+)?)\s*\+?\s*(?:years?|yrs?)?(?:\s*(?:&|and)?\s*(\d+)\s*months?)?\s*",
    re.IGNORECASE,
)
_MONTHS_ONLY = re.compile(r"\s*(\d+)\s*months?\s*", re.IGNORECASE)
_MARKDOWN_LINK = re.compile(r"\((https?://[^)\s]+)\)")
_TIMESTAMP_FORMATS = ("%B %d, %Y %I:%M %p", "%B %d, %Y %I %p", "%m/%d/%Y")


def text(value: str) -> str | None:
    return value.strip() or None


def link(value: str) -> str | None:
    """Return the URL from a markdown link `[url](url)`, or the plain text."""
    value = value.strip()
    match = _MARKDOWN_LINK.search(value)
    return match.group(1) if match else (value or None)


def timestamp(value: str) -> datetime | None:
    """Parse `July 29, 2026 at 3:01 PM IST`, `... at 9 PM IST` or `8/31/2026` (month/day/year).

    Date-only values are stored as midnight IST.
    """
    cleaned = re.sub(r" IST$", "", value.strip()).replace(" at ", " ")
    for fmt in _TIMESTAMP_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt).replace(tzinfo=IST)
        except ValueError:
            continue
    return None


def number(value: str) -> Decimal | None:
    """Parse `3.3` or `8+`. Ranges such as `5-7` return None."""
    match = _NUMBER.fullmatch(value)
    return Decimal(match.group(1)) if match else None


def integer(value: str) -> int | None:
    parsed = number(value)
    return int(parsed) if parsed is not None and parsed == parsed.to_integral() else None


def experience(value: str) -> Decimal | None:
    """Parse experience into years: `8+`, `2 Years`, `6 Years & 6 Months`, `8 Months`.

    Google Sheets auto-converted some cells to dates (e.g. `1/2/1900`); those are
    unrecoverable and return None.
    """
    match = _EXPERIENCE.fullmatch(value)
    if match:
        years = Decimal(match.group(1))
        if match.group(2):
            years += Decimal(match.group(2)) / 12
        return years.quantize(_TWO_PLACES)
    match = _MONTHS_ONLY.fullmatch(value)
    return (Decimal(match.group(1)) / 12).quantize(_TWO_PLACES) if match else None
