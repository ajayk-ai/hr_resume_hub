"""Read the candidate sheet through the Google Sheets API."""
import logging

import gspread
from google.oauth2.service_account import Credentials

from hr_resume_hub.config import Settings
from hr_resume_hub.schema import EXPECTED_HEADERS

logger = logging.getLogger(__name__)


class SheetSchemaError(Exception):
    """The sheet's columns no longer match the expected layout."""


def fetch_rows(settings: Settings) -> list[list[str]]:
    """Return the data rows of the first worksheet, with the header row removed."""
    credentials = Credentials.from_service_account_file(
        settings.service_account_file,
        scopes=[settings.sheets_scope],
        subject=settings.delegated_user,
    )
    worksheet = gspread.authorize(credentials).open_by_key(settings.spreadsheet_id).sheet1
    header, *rows = worksheet.get_all_values()
    if tuple(header[: len(EXPECTED_HEADERS)]) != EXPECTED_HEADERS:
        raise SheetSchemaError(
            "Sheet header changed.\n"
            f"  expected: {list(EXPECTED_HEADERS)}\n"
            f"  found:    {header}"
        )
    logger.info("Read %d data rows from sheet %s", len(rows), worksheet.spreadsheet.title)
    return rows
