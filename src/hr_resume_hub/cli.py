"""Command-line entry point."""
import argparse
import logging
import sys
from collections.abc import Sequence

from hr_resume_hub.config import ConfigError, Settings
from hr_resume_hub.pipeline import run
from hr_resume_hub.sheets import SheetSchemaError

logger = logging.getLogger("hr_resume_hub")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="hr-resume-hub",
        description="Load the consolidated HR resume Google Sheet into PostgreSQL.",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="read and parse only; do not write to the database"
    )
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args(argv)

    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    try:
        run(Settings.from_env(), dry_run=args.dry_run)
    except (ConfigError, SheetSchemaError) as error:
        logger.error("%s", error)
        return 2
    except Exception:
        logger.exception("Run failed")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
