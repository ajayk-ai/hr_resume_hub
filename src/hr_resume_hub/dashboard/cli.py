"""Command-line entry point for the dashboard (`hr-dashboard`)."""
import argparse
import getpass
import logging
import sys
from collections.abc import Sequence
from contextlib import contextmanager

import uvicorn
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from hr_resume_hub.config import ConfigError, DashboardSettings
from hr_resume_hub.dashboard import auth, repository
from hr_resume_hub.dashboard.app import create_app

logger = logging.getLogger("hr_resume_hub.dashboard")


@contextmanager
def _pool(settings: DashboardSettings):
    pool = ConnectionPool(
        settings.database.conninfo(), min_size=1, max_size=2, open=True, kwargs={"row_factory": dict_row}
    )
    try:
        yield pool
    finally:
        pool.close()


def _read_password() -> str:
    try:
        password = auth.check_password(getpass.getpass("Password: "))
    except ValueError as error:
        raise SystemExit(str(error)) from None
    if password != getpass.getpass("Repeat password: "):
        raise SystemExit("Passwords do not match.")
    return password


def _create_user(settings: DashboardSettings, args: argparse.Namespace) -> int:
    try:
        username = auth.normalise_username(args.username)
        display_name = auth.clean_display_name(args.name)
    except ValueError as error:
        raise SystemExit(str(error)) from None
    password = _read_password()
    with _pool(settings) as pool:
        repository.ensure_tables(pool, settings.database.schema)
        repository.upsert_user(
            pool, settings.database.schema, username, display_name, args.role, auth.hash_password(password)
        )
    print(f"Saved {args.role.upper()} user '{username}'.")
    return 0


def _list_users(settings: DashboardSettings, args: argparse.Namespace) -> int:
    with _pool(settings) as pool:
        repository.ensure_tables(pool, settings.database.schema)
        users = repository.list_users(pool, settings.database.schema)
    for user in users:
        status = "active" if user["active"] else "disabled"
        print(f"{user['username']:<24} {user['role']:<4} {status:<9} {user['display_name']}")
    if not users:
        print("No users yet. Create an admin with: hr-dashboard create-user --username ... --name ... --role admin")
    return 0


def _set_active(settings: DashboardSettings, args: argparse.Namespace, active: bool) -> int:
    with _pool(settings) as pool:
        repository.ensure_tables(pool, settings.database.schema)
        found = repository.set_user_active(pool, settings.database.schema, args.username.strip().lower(), active)
    if not found:
        raise SystemExit(f"No such user: {args.username}")
    print(f"User '{args.username}' {'enabled' if active else 'disabled'}.")
    return 0


def _serve(settings: DashboardSettings, args: argparse.Namespace) -> int:
    host = args.host or settings.host
    port = args.port or settings.port
    if host not in {"127.0.0.1", "localhost", "::1"} and not settings.https_only:
        logger.warning(
            "Listening on %s without HTTPS. Passwords and candidate data would cross the network in clear text; "
            "put the dashboard behind an HTTPS reverse proxy and set DASHBOARD_HTTPS_ONLY=true.",
            host,
        )
    uvicorn.run(create_app(settings), host=host, port=port, log_level="info")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hr-dashboard", description="Candidate dashboard for HR and HOD.")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    commands = parser.add_subparsers(dest="command", required=True)

    serve = commands.add_parser("serve", help="start the web server")
    serve.add_argument("--host", help="default: DASHBOARD_HOST or 127.0.0.1")
    serve.add_argument("--port", type=int, help="default: DASHBOARD_PORT or 8000")

    create = commands.add_parser("create-user", help="create a user, or reset an existing user's password")
    create.add_argument("--username", required=True)
    create.add_argument("--name", required=True, help="display name shown next to stars")
    create.add_argument("--role", required=True, choices=repository.USER_ROLES)

    commands.add_parser("list-users", help="list users")
    disable = commands.add_parser("disable-user", help="block a user from signing in")
    disable.add_argument("--username", required=True)
    enable = commands.add_parser("enable-user", help="re-enable a disabled user")
    enable.add_argument("--username", required=True)

    args = parser.parse_args(argv)
    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    try:
        settings = DashboardSettings.from_env()
        handlers = {
            "serve": lambda: _serve(settings, args),
            "create-user": lambda: _create_user(settings, args),
            "list-users": lambda: _list_users(settings, args),
            "disable-user": lambda: _set_active(settings, args, False),
            "enable-user": lambda: _set_active(settings, args, True),
        }
        return handlers[args.command]()
    except (ConfigError, repository.SetupError) as error:
        logger.error("%s", error)
        return 2


if __name__ == "__main__":
    sys.exit(main())
