"""PostgreSQL queries behind the dashboard.

The schema name is validated in DatabaseSettings, so it is safe to embed in SQL text.
Every user-supplied value is passed as a query parameter.
"""
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from psycopg_pool import ConnectionPool

STAR_ROLES = ("hr", "hod")  # roles that review candidates and can star them
USER_ROLES = (*STAR_ROLES, "admin")  # admin manages users only and never sees candidate data
PAGE_SIZES = (25, 50, 100)

# Public sort keys -> SQL expressions (whitelist: never interpolate a client value).
SORT_COLUMNS = {
    "name": "c.candidate_name",
    "position": "c.applied_position",
    "department": "c.department",
    "experience": "c.total_experience_years",
    "score": "c.ai_match_score",
    "received": "c.received_at",
    "hr_star": "hr.starred_at",
    "hod_star": "hod.starred_at",
}
OPTION_COLUMNS = {"position": "applied_position", "department": "department"}

SEARCH_EXPR = (
    "concat_ws(' ', c.candidate_name, c.email, c.phone, c.current_company, "
    "c.current_designation, c.applied_position, c.key_skills)"
)
LIST_COLUMNS = """
    c.candidate_key, c.candidate_name, c.email, c.phone, c.applied_position, c.department,
    c.current_location, c.preferred_location, c.current_company, c.current_designation,
    c.total_experience_years, c.relevant_experience_years, c.notice_period, c.current_ctc,
    c.expected_ctc, c.highest_qualification, c.specialization, c.ai_match_score, c.received_at,
    c.resume_link, c.key_skills, c.ai_resume_summary, c.ai_strengths, c.ai_weaknesses,
    c.ai_skill_gaps, c.remarks, c.data_source,
    coalesce(hru.display_name, hr.starred_by) AS hr_by, hr.starred_at AS hr_at,
    coalesce(hodu.display_name, hod.starred_by) AS hod_by, hod.starred_at AS hod_at
"""
_STAR_FIELDS = {"hr_by", "hr_at", "hod_by", "hod_at"}


class SetupError(Exception):
    """The database is not ready for the dashboard (for example, no candidates loaded yet)."""


@dataclass(frozen=True)
class Filters:
    q: str = ""
    positions: tuple[str, ...] = ()  # exact values ticked in the list
    position_terms: tuple[str, ...] = ()  # "contains" searches, e.g. "sap pp"
    departments: tuple[str, ...] = ()
    department_terms: tuple[str, ...] = ()
    exp_min: float | None = None
    exp_max: float | None = None
    score_min: int | None = None
    received_from: date | None = None
    received_to: date | None = None
    hr_star: bool | None = None  # True = starred, False = not starred, None = any
    hod_star: bool | None = None


def like_pattern(token: str) -> str:
    escaped = token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _patterns(text: str) -> list[str]:
    """Split a search phrase into word patterns; a row must contain every word (any order)."""
    return [like_pattern(word) for word in text.split()]


def _match_clause(column: str, exact: tuple[str, ...], terms: tuple[str, ...], params: list[Any]) -> str | None:
    parts = []
    if exact:
        parts.append(f"{column} = ANY(%s)")
        params.append(list(exact))
    for term in terms:
        patterns = _patterns(term)
        if patterns:
            parts.append(f"{column} ILIKE ALL (%s)")
            params.append(patterns)
    return "(" + " OR ".join(parts) + ")" if parts else None


def build_where(filters: Filters) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []

    for pattern in _patterns(filters.q):
        clauses.append(f"{SEARCH_EXPR} ILIKE %s")
        params.append(pattern)

    for column, exact, terms in (
        ("c.applied_position", filters.positions, filters.position_terms),
        ("c.department", filters.departments, filters.department_terms),
    ):
        clause = _match_clause(column, exact, terms, params)
        if clause:
            clauses.append(clause)

    for template, value in (
        ("c.total_experience_years >= %s", filters.exp_min),
        ("c.total_experience_years <= %s", filters.exp_max),
        ("c.ai_match_score >= %s", filters.score_min),
        ("(c.received_at AT TIME ZONE 'Asia/Kolkata')::date >= %s", filters.received_from),
        ("(c.received_at AT TIME ZONE 'Asia/Kolkata')::date <= %s", filters.received_to),
    ):
        if value is not None:
            clauses.append(template)
            params.append(value)

    for alias, wanted in (("hr", filters.hr_star), ("hod", filters.hod_star)):
        if wanted is not None:
            clauses.append(f"{alias}.candidate_key IS {'NOT ' if wanted else ''}NULL")

    return ("WHERE " + " AND ".join(clauses)) if clauses else "", params


def _from(schema: str) -> str:
    return f"""
        FROM {schema}.v_candidates c
        LEFT JOIN {schema}.candidate_stars hr ON hr.candidate_key = c.candidate_key AND hr.role = 'hr'
        LEFT JOIN {schema}.dashboard_users hru ON hru.username = hr.starred_by
        LEFT JOIN {schema}.candidate_stars hod ON hod.candidate_key = c.candidate_key AND hod.role = 'hod'
        LEFT JOIN {schema}.dashboard_users hodu ON hodu.username = hod.starred_by
    """


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    return value


def _item(row: dict[str, Any]) -> dict[str, Any]:
    item = {key: _json_value(value) for key, value in row.items() if key not in _STAR_FIELDS}
    for role in STAR_ROLES:
        item[f"{role}_star"] = (
            {"by": row[f"{role}_by"], "at": row[f"{role}_at"].isoformat()} if row[f"{role}_at"] else None
        )
    return item


def search(
    pool: ConnectionPool, schema: str, filters: Filters, sort: str, direction: str, page: int, page_size: int
) -> dict[str, Any]:
    where, params = build_where(filters)
    order = f"{SORT_COLUMNS[sort]} {'ASC' if direction == 'asc' else 'DESC'} NULLS LAST, c.candidate_key"
    source = _from(schema)
    with pool.connection() as conn:
        summary = conn.execute(
            f"""SELECT count(*) AS total,
                       count(hr.candidate_key) AS hr_starred,
                       count(hod.candidate_key) AS hod_starred,
                       count(*) FILTER (WHERE hr.candidate_key IS NOT NULL
                                          AND hod.candidate_key IS NOT NULL) AS both_starred
                {source} {where}""",
            params,
        ).fetchone()
        rows = conn.execute(
            f"SELECT {LIST_COLUMNS} {source} {where} ORDER BY {order} LIMIT %s OFFSET %s",
            [*params, page_size, (page - 1) * page_size],
        ).fetchall()
        overall = conn.execute(f"SELECT count(*) AS n FROM {schema}.v_candidates").fetchone()["n"]
    return {"items": [_item(row) for row in rows], "summary": {**summary, "overall_total": overall}}


def summary(pool: ConnectionPool, schema: str, filters: Filters) -> dict[str, Any]:
    return search(pool, schema, filters, "received", "desc", 1, 1)["summary"]


def options(pool: ConnectionPool, schema: str, field: str, query: str, limit: int = 50) -> dict[str, Any]:
    """Distinct values of a free-text column matching `query`, most common first."""
    column = OPTION_COLUMNS[field]
    patterns = _patterns(query)
    match = f"AND {column} ILIKE ALL (%s)" if patterns else ""
    with pool.connection() as conn:
        rows = conn.execute(
            f"""SELECT {column} AS value, count(*) AS n,
                       count(*) OVER () AS total_options,
                       sum(count(*)) OVER () AS total_candidates
                FROM {schema}.v_candidates
                WHERE {column} IS NOT NULL AND btrim({column}) <> '' {match}
                GROUP BY {column} ORDER BY n DESC, {column} LIMIT %s""",
            [*([patterns] if patterns else []), limit],
        ).fetchall()
    return {
        "options": [{"value": row["value"], "count": row["n"]} for row in rows],
        "total_options": rows[0]["total_options"] if rows else 0,
        "total_candidates": int(rows[0]["total_candidates"]) if rows else 0,
    }


def get_stars(pool: ConnectionPool, schema: str, key: str) -> dict[str, Any]:
    with pool.connection() as conn:
        rows = conn.execute(
            f"""SELECT s.role, coalesce(u.display_name, s.starred_by) AS by, s.starred_at AS at
                FROM {schema}.candidate_stars s
                LEFT JOIN {schema}.dashboard_users u ON u.username = s.starred_by
                WHERE s.candidate_key = %s""",
            [key],
        ).fetchall()
    stars: dict[str, Any] = {role: None for role in STAR_ROLES}
    for row in rows:
        stars[row["role"]] = {"by": row["by"], "at": row["at"].isoformat()}
    return stars


def set_star(pool: ConnectionPool, schema: str, key: str, role: str, username: str, starred: bool) -> bool:
    """Star or unstar a candidate for `role`. Returns False when the candidate does not exist.

    The first star wins: starring an already-starred candidate keeps the original attribution.
    """
    with pool.connection() as conn:
        exists = conn.execute(f"SELECT 1 FROM {schema}.v_candidates WHERE candidate_key = %s", [key]).fetchone()
        if not exists:
            return False
        if starred:
            conn.execute(
                f"""INSERT INTO {schema}.candidate_stars (candidate_key, role, starred_by)
                    VALUES (%s, %s, %s) ON CONFLICT (candidate_key, role) DO NOTHING""",
                [key, role, username],
            )
        else:
            conn.execute(
                f"DELETE FROM {schema}.candidate_stars WHERE candidate_key = %s AND role = %s", [key, role]
            )
    return True


# ---- users -----------------------------------------------------------------------------------


def get_user(pool: ConnectionPool, schema: str, username: str) -> dict[str, Any] | None:
    with pool.connection() as conn:
        return conn.execute(
            f"SELECT username, display_name, role, password_hash, active, created_at "
            f"FROM {schema}.dashboard_users WHERE username = %s",
            [username],
        ).fetchone()


def create_user(
    pool: ConnectionPool, schema: str, username: str, display_name: str, role: str, password_hash: str
) -> bool:
    """Insert a new user. Returns False when the username is already taken."""
    with pool.connection() as conn:
        cursor = conn.execute(
            f"""INSERT INTO {schema}.dashboard_users (username, display_name, role, password_hash)
                VALUES (%s, %s, %s, %s) ON CONFLICT (username) DO NOTHING""",
            [username, display_name, role, password_hash],
        )
        return cursor.rowcount > 0


def update_user(
    pool: ConnectionPool,
    schema: str,
    username: str,
    *,
    display_name: str | None = None,
    role: str | None = None,
    active: bool | None = None,
    password_hash: str | None = None,
) -> bool:
    """Change the given fields of a user. Returns False when the user does not exist."""
    changes = {
        "display_name": display_name,
        "role": role,
        "active": active,
        "password_hash": password_hash,
    }
    assignments = [f"{column} = %s" for column, value in changes.items() if value is not None]
    if not assignments:
        return get_user(pool, schema, username) is not None
    values = [value for value in changes.values() if value is not None]
    with pool.connection() as conn:
        cursor = conn.execute(
            f"UPDATE {schema}.dashboard_users SET {', '.join(assignments)} WHERE username = %s",
            [*values, username],
        )
        return cursor.rowcount > 0


def upsert_user(
    pool: ConnectionPool, schema: str, username: str, display_name: str, role: str, password_hash: str
) -> None:
    with pool.connection() as conn:
        conn.execute(
            f"""INSERT INTO {schema}.dashboard_users (username, display_name, role, password_hash)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (username) DO UPDATE
                SET display_name = EXCLUDED.display_name, role = EXCLUDED.role,
                    password_hash = EXCLUDED.password_hash, active = true""",
            [username, display_name, role, password_hash],
        )


def set_user_active(pool: ConnectionPool, schema: str, username: str, active: bool) -> bool:
    with pool.connection() as conn:
        cursor = conn.execute(
            f"UPDATE {schema}.dashboard_users SET active = %s WHERE username = %s", [active, username]
        )
        return cursor.rowcount > 0


def list_users(pool: ConnectionPool, schema: str) -> list[dict[str, Any]]:
    with pool.connection() as conn:
        return conn.execute(
            f"SELECT username, display_name, role, active, created_at FROM {schema}.dashboard_users "
            "ORDER BY username"
        ).fetchall()


# ---- schema ----------------------------------------------------------------------------------


def ensure_tables(pool: ConnectionPool, schema: str) -> None:
    """Create the user and star tables (independent of the candidates table)."""
    with pool.connection() as conn:
        conn.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
        conn.execute(
            f"""CREATE TABLE IF NOT EXISTS {schema}.dashboard_users (
                    username text PRIMARY KEY,
                    display_name text NOT NULL,
                    role text NOT NULL CONSTRAINT dashboard_users_role_check CHECK (role IN ('hr', 'hod', 'admin')),
                    password_hash text NOT NULL,
                    active boolean NOT NULL DEFAULT true,
                    created_at timestamptz NOT NULL DEFAULT now())"""
        )
        # Tables created before the admin role existed only allow 'hr' and 'hod'.
        constraint = conn.execute(
            "SELECT pg_get_constraintdef(oid) AS definition FROM pg_constraint "
            "WHERE conrelid = %s::regclass AND conname = 'dashboard_users_role_check'",
            [f"{schema}.dashboard_users"],
        ).fetchone()
        if not constraint or "admin" not in constraint["definition"]:
            conn.execute(f"ALTER TABLE {schema}.dashboard_users DROP CONSTRAINT IF EXISTS dashboard_users_role_check")
            conn.execute(
                f"ALTER TABLE {schema}.dashboard_users ADD CONSTRAINT dashboard_users_role_check "
                "CHECK (role IN ('hr', 'hod', 'admin'))"
            )
        conn.execute(
            f"""CREATE TABLE IF NOT EXISTS {schema}.candidate_stars (
                    candidate_key text NOT NULL,
                    role text NOT NULL CHECK (role IN ('hr', 'hod')),
                    starred_by text NOT NULL REFERENCES {schema}.dashboard_users (username),
                    starred_at timestamptz NOT NULL DEFAULT now(),
                    PRIMARY KEY (candidate_key, role))"""
        )


def init_schema(pool: ConnectionPool, schema: str) -> None:
    """Create tables and the latest-row-per-candidate view the dashboard reads from."""
    ensure_tables(pool, schema)
    with pool.connection() as conn:
        has_key = conn.execute(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = 'candidates' AND column_name = 'candidate_key'",
            [schema],
        ).fetchone()
        if not has_key:
            raise SetupError(
                f"{schema}.candidates has no candidate_key column. Run `uv run hr-resume-hub` to (re)load the data."
            )
        conn.execute(f"DROP VIEW IF EXISTS {schema}.v_candidates")
        conn.execute(
            f"""CREATE VIEW {schema}.v_candidates AS
                SELECT DISTINCT ON (candidate_key) *
                FROM {schema}.candidates
                ORDER BY candidate_key, received_at DESC NULLS LAST, id DESC"""
        )
