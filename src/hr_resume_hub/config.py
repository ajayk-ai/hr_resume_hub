"""Runtime configuration, read from environment variables and an optional .env file."""
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import find_dotenv, load_dotenv
from psycopg.conninfo import make_conninfo

DEFAULT_SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class ConfigError(Exception):
    """A required setting is missing or invalid."""


def _load_env() -> Path:
    """Load .env (if present) and return the directory that relative paths resolve against."""
    dotenv_path = find_dotenv(usecwd=True)
    if dotenv_path:
        load_dotenv(dotenv_path)
    return Path(dotenv_path).parent if dotenv_path else Path.cwd()


def _first(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return None


def _require(required: dict[str, str | None]) -> None:
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise ConfigError("Missing required settings: " + ", ".join(missing))


def _int(name: str, default: str) -> int:
    value = _first(name) or default
    if not value.isdigit():
        raise ConfigError(f"{name} must be a number, got {value!r}")
    return int(value)


@dataclass(frozen=True)
class DatabaseSettings:
    host: str
    port: int
    name: str
    user: str
    password: str = field(repr=False)
    schema: str

    @classmethod
    def from_env(cls) -> DatabaseSettings:
        _load_env()
        return cls.from_environ()

    @classmethod
    def from_environ(cls) -> DatabaseSettings:
        user = _first("HR_DB_USER", "PO_DB_USER")
        password = _first("HR_DB_PASSWORD", "PO_DB_PASSWORD")
        name = _first("HR_DB_NAME")
        _require(
            {
                "HR_DB_NAME": name,
                "HR_DB_USER (or PO_DB_USER)": user,
                "HR_DB_PASSWORD (or PO_DB_PASSWORD)": password,
            }
        )
        schema = _first("HR_DB_SCHEMA") or "resume_hub"
        if not _IDENTIFIER.fullmatch(schema):
            raise ConfigError(f"HR_DB_SCHEMA must be a plain identifier, got {schema!r}")
        return cls(
            host=_first("HR_DB_HOST") or "localhost",
            port=_int("HR_DB_PORT", "5432"),
            name=name,
            user=user,
            password=password,
            schema=schema,
        )

    def conninfo(self) -> str:
        return make_conninfo(
            host=self.host, port=self.port, dbname=self.name, user=self.user, password=self.password
        )


@dataclass(frozen=True)
class Settings:
    """Settings for the sheet-to-database loader."""

    service_account_file: Path
    delegated_user: str | None
    sheets_scope: str
    spreadsheet_id: str
    database: DatabaseSettings

    @classmethod
    def from_env(cls) -> Settings:
        base_dir = _load_env()
        key_file = _first("GOOGLE_SERVICE_ACCOUNT_FILE")
        sheet_id = _first("HR_SHEET_ID", "CONSOLATION_SHEET")
        _require({"GOOGLE_SERVICE_ACCOUNT_FILE": key_file, "HR_SHEET_ID (or CONSOLATION_SHEET)": sheet_id})

        service_account_file = Path(key_file)
        if not service_account_file.is_absolute():
            service_account_file = base_dir / service_account_file
        if not service_account_file.is_file():
            raise ConfigError(f"Service account file not found: {service_account_file}")

        return cls(
            service_account_file=service_account_file,
            delegated_user=_first("GOOGLE_DELEGATED_USER"),
            sheets_scope=_first("GOOGLE_SHEETS_SCOPE") or DEFAULT_SCOPE,
            spreadsheet_id=sheet_id,
            database=DatabaseSettings.from_environ(),
        )


@dataclass(frozen=True)
class DashboardSettings:
    """Settings for the web dashboard."""

    database: DatabaseSettings
    secret_key: str = field(repr=False)
    host: str
    port: int
    https_only: bool
    session_hours: int

    @classmethod
    def from_env(cls) -> DashboardSettings:
        _load_env()
        secret = _first("DASHBOARD_SECRET_KEY")
        if not secret or len(secret) < 32:
            raise ConfigError(
                "DASHBOARD_SECRET_KEY must be a random string of at least 32 characters. Generate one with: "
                'python -c "import secrets; print(secrets.token_urlsafe(48))"'
            )
        return cls(
            database=DatabaseSettings.from_environ(),
            secret_key=secret,
            host=_first("DASHBOARD_HOST") or "127.0.0.1",
            port=_int("DASHBOARD_PORT", "8000"),
            https_only=(_first("DASHBOARD_HTTPS_ONLY") or "false").lower() in {"1", "true", "yes"},
            session_hours=_int("DASHBOARD_SESSION_HOURS", "8"),
        )
