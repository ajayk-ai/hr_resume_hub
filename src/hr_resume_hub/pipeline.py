"""Orchestrate one sheet-to-database run."""
import logging

from hr_resume_hub.config import Settings
from hr_resume_hub.database import replace_candidates
from hr_resume_hub.sheets import fetch_rows
from hr_resume_hub.transform import parse_rows

logger = logging.getLogger(__name__)


def run(settings: Settings, *, dry_run: bool = False) -> int:
    """Read the sheet, parse it and (unless dry_run) reload the table. Returns the row count."""
    result = parse_rows(fetch_rows(settings))
    logger.info("Parsed %d rows", len(result.records))
    for column, count in sorted(result.unparseable.items()):
        logger.warning("%s: %d value(s) not parseable, stored as NULL", column, count)
    if dry_run:
        logger.info("Dry run: database not touched")
        return len(result.records)
    return replace_candidates(settings.database, result.records)
