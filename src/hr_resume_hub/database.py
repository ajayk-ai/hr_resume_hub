"""Write candidate records to PostgreSQL."""
import logging

import psycopg
from psycopg import sql

from hr_resume_hub.config import DatabaseSettings
from hr_resume_hub.schema import DB_COLUMNS, TABLE_NAME

logger = logging.getLogger(__name__)


def replace_candidates(db: DatabaseSettings, records: list[list[object]]) -> int:
    """Replace the whole candidates table with `records` in a single transaction.

    If anything fails the transaction rolls back and the previous data stays intact.
    Columns added to the schema later are added to an existing table automatically.
    """
    schema = sql.Identifier(db.schema)
    table = sql.SQL("{}.{}").format(schema, sql.Identifier(TABLE_NAME))
    names = sql.SQL(", ").join(sql.Identifier(name) for name, _ in DB_COLUMNS)
    definitions = sql.SQL(", ").join(
        sql.SQL("{} {}").format(sql.Identifier(name), sql.SQL(pg_type)) for name, pg_type in DB_COLUMNS
    )

    with psycopg.connect(db.conninfo()) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(schema))
        conn.execute(
            sql.SQL(
                "CREATE TABLE IF NOT EXISTS {} (id bigserial PRIMARY KEY, {}, "
                "loaded_at timestamptz NOT NULL DEFAULT now())"
            ).format(table, definitions)
        )
        for name, pg_type in DB_COLUMNS:  # evolve tables created by an older version
            conn.execute(
                sql.SQL("ALTER TABLE {} ADD COLUMN IF NOT EXISTS {} {}").format(
                    table, sql.Identifier(name), sql.SQL(pg_type)
                )
            )
        conn.execute(
            sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} (candidate_key)").format(
                sql.Identifier(f"{TABLE_NAME}_candidate_key_idx"), table
            )
        )
        conn.execute(sql.SQL("TRUNCATE {} RESTART IDENTITY").format(table))
        copy_sql = sql.SQL("COPY {} ({}) FROM STDIN").format(table, names)
        with conn.cursor() as cursor, cursor.copy(copy_sql) as copy:
            for record in records:
                copy.write_row(record)
    logger.info("Loaded %d rows into %s.%s", len(records), db.schema, TABLE_NAME)
    return len(records)
