"""Turn raw sheet rows into typed records ready for loading."""
import hashlib
import re
from collections import Counter
from dataclasses import dataclass, field

from hr_resume_hub.schema import SHEET_COLUMNS, STORED_COLUMNS

_INDEX = {column.name: i for i, column in enumerate(STORED_COLUMNS)}


@dataclass
class ParseResult:
    records: list[list[object]] = field(default_factory=list)
    unparseable: Counter[str] = field(default_factory=Counter)  # column name -> count


def candidate_key(
    email: str | None,
    phone: str | None,
    name: str | None,
    position: str | None,
    resume_link: str | None = None,
) -> str:
    """Stable id for one candidate applying for one position.

    The table is reloaded on every run, so row ids change; this key does not. Stars attach to it.
    Identity is the email, else the last 10 phone digits, else the name, else the resume link.
    Changing this rule orphans existing stars, so do not change it casually.
    """
    digits = re.sub(r"\D", "", phone or "")[-10:]
    identity = (email or "").lower() or digits or " ".join((name or "").lower().split()) or (resume_link or "")
    role = " ".join((position or "").lower().split())
    return hashlib.sha256(f"{identity}|{role}".encode()).hexdigest()[:32]


def parse_rows(rows: list[list[str]]) -> ParseResult:
    """Parse data rows (header excluded). Blank rows are skipped.

    A cell that has text but cannot be parsed is stored as None and counted in
    `unparseable`, so data loss is visible rather than silent.
    """
    result = ParseResult()
    width = len(SHEET_COLUMNS)
    for row in rows:
        cells = row[:width] + [""] * (width - len(row))
        if not any(cell.strip() for cell in cells):
            continue
        record = []
        for column, cell in zip(SHEET_COLUMNS, cells, strict=True):
            if column.name is None:
                continue
            value = column.parser(cell)
            if value is None and cell.strip():
                result.unparseable[column.name] += 1
            record.append(value)
        record.append(
            candidate_key(
                record[_INDEX["email"]],
                record[_INDEX["phone"]],
                record[_INDEX["candidate_name"]],
                record[_INDEX["applied_position"]],
                record[_INDEX["resume_link"]],
            )
        )
        result.records.append(record)
    return result
