"""Mapping between the Google Sheet columns and the PostgreSQL table."""
from collections.abc import Callable
from dataclasses import dataclass

from hr_resume_hub import parsers

TABLE_NAME = "candidates"


@dataclass(frozen=True)
class Column:
    header: str  # header text in the sheet
    name: str | None  # database column; None when the sheet column is not stored
    pg_type: str | None
    parser: Callable[[str], object] | None


# Order must match the sheet, left to right.
SHEET_COLUMNS: tuple[Column, ...] = (
    Column("Received Date", "received_at", "timestamptz", parsers.timestamp),
    Column("Received Time", None, None, None),  # duplicate of Received Date
    Column("Candidate Name", "candidate_name", "text", parsers.text),
    Column("Email ID", "email", "text", parsers.text),
    Column("Phone Number", "phone", "text", parsers.text),
    Column("Current Location", "current_location", "text", parsers.text),
    Column("Preferred Location", "preferred_location", "text", parsers.text),
    Column("Applied Position", "applied_position", "text", parsers.text),
    Column("Department", "department", "text", parsers.text),
    Column("Source", "source", "text", parsers.text),
    Column("Resume File Name", "resume_file_name", "text", parsers.text),
    Column("Resume Link", "resume_link", "text", parsers.link),
    Column("Total Experience (Years)", "total_experience_years", "numeric", parsers.experience),
    Column("Relevant Experience (Years)", "relevant_experience_years", "numeric", parsers.experience),
    Column("Current Company", "current_company", "text", parsers.text),
    Column("Current Designation", "current_designation", "text", parsers.text),
    Column("Highest Qualification", "highest_qualification", "text", parsers.text),
    Column("Specialization", "specialization", "text", parsers.text),
    Column("Current CTC", "current_ctc", "text", parsers.text),
    Column("Expected CTC", "expected_ctc", "text", parsers.text),
    # Free text in the sheet ("30 Days", "Serving Notice Period"); kept as written.
    Column("Notice Period (Days)", "notice_period", "text", parsers.text),
    Column("Key Skills", "key_skills", "text", parsers.text),
    Column("AI Resume Summary", "ai_resume_summary", "text", parsers.text),
    Column("AI Strengths", "ai_strengths", "text", parsers.text),
    Column("AI Weaknesses", "ai_weaknesses", "text", parsers.text),
    Column("AI Skill Gaps", "ai_skill_gaps", "text", parsers.text),
    Column("AI Match Score (%)", "ai_match_score", "integer", parsers.integer),
    Column("Remarks", "remarks", "text", parsers.text),
    Column("Data Source", "data_source", "text", parsers.text),
)

STORED_COLUMNS: tuple[Column, ...] = tuple(c for c in SHEET_COLUMNS if c.name)
EXPECTED_HEADERS: tuple[str, ...] = tuple(c.header for c in SHEET_COLUMNS)

# Columns computed while parsing; they do not exist in the sheet.
DERIVED_COLUMNS: tuple[tuple[str, str], ...] = (("candidate_key", "text"),)

# Every column written to the database, in load order: (name, Postgres type).
DB_COLUMNS: tuple[tuple[str, str], ...] = (
    tuple((c.name, c.pg_type) for c in STORED_COLUMNS) + DERIVED_COLUMNS
)
