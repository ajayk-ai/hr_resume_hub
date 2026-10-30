from hr_resume_hub.schema import DB_COLUMNS, EXPECTED_HEADERS, SHEET_COLUMNS, STORED_COLUMNS
from hr_resume_hub.transform import candidate_key, parse_rows


def make_row(**cells):
    """Build a sheet row of the right width from synthetic values keyed by sheet header."""
    return [cells.get(header, "") for header in EXPECTED_HEADERS]


def test_schema_is_consistent():
    assert len(EXPECTED_HEADERS) == 29
    assert len(STORED_COLUMNS) == 28  # Received Time is not stored
    assert len({c.name for c in STORED_COLUMNS}) == len(STORED_COLUMNS)
    assert all(c.parser and c.pg_type for c in STORED_COLUMNS)
    assert [name for name, _ in DB_COLUMNS][-1] == "candidate_key"


def test_parse_rows_skips_blank_rows_and_pads_short_rows():
    full = make_row(**{"Candidate Name": "Test Candidate", "Total Experience (Years)": "8+"})
    short = ["8/31/2026", "", "Short Row"]
    result = parse_rows([full, [""] * len(SHEET_COLUMNS), [], short])

    assert len(result.records) == 2
    assert all(len(record) == len(DB_COLUMNS) for record in result.records)


def test_parse_rows_counts_unparseable_values_not_blanks():
    rows = [
        make_row(**{"Relevant Experience (Years)": "1/2/1900"}),  # present but unparseable
        make_row(**{"Candidate Name": "Test", "Relevant Experience (Years)": ""}),  # blank is fine
    ]
    result = parse_rows(rows)

    assert result.unparseable == {"relevant_experience_years": 1}
    names = [c.name for c in STORED_COLUMNS]
    assert result.records[0][names.index("relevant_experience_years")] is None


def test_candidate_key_is_stable_and_normalised():
    base = candidate_key("A.B@Example.com", None, "A B", "Design  Engineer")
    assert base == candidate_key("a.b@example.com", "+91 90000 00000", "Someone Else", "design engineer")
    assert len(base) == 32


def test_candidate_key_separates_positions_and_people():
    key = candidate_key("a@example.com", None, None, "Design Engineer")
    assert key != candidate_key("a@example.com", None, None, "Senior Engineer")
    assert key != candidate_key("b@example.com", None, None, "Design Engineer")


def test_candidate_key_falls_back_to_phone_then_name():
    assert candidate_key(None, "+91-97158 37840", "X", "P") == candidate_key(None, "9715837840", "Y", "P")
    assert candidate_key(None, None, "Test  Name", "P") == candidate_key(None, None, "test name", "P")


def test_same_person_and_position_gets_the_same_key_in_parse_rows():
    cells = {"Email ID": "a@example.com", "Applied Position": "Design Engineer"}
    result = parse_rows([make_row(**cells), make_row(**cells, **{"Remarks": "second application"})])
    assert result.records[0][-1] == result.records[1][-1]
