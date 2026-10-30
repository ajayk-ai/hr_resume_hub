"""The dashboard web application (Starlette)."""
import functools
import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from hr_resume_hub.config import DashboardSettings
from hr_resume_hub.dashboard import auth, repository

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
AJAX_HEADER = ("x-requested-with", "hr-dashboard")  # blocks cross-site form posts
CANDIDATE_KEY = re.compile(r"[0-9a-f]{32}")
MAX_VALUES = 100
MAX_VALUE_LENGTH = 300
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}


class ApiError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers.update(SECURITY_HEADERS)
        if request.url.path == "/" or request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"  # candidate data must not be cached
        return response


def _require_ajax(request: Request) -> None:
    if request.headers.get(AJAX_HEADER[0]) != AJAX_HEADER[1]:
        raise ApiError(400, "Missing request header.")


async def _json_body(request: Request) -> dict:
    try:
        body = await request.json()
    except ValueError:
        raise ApiError(400, "Request body must be JSON.") from None
    if not isinstance(body, dict):
        raise ApiError(400, "Request body must be a JSON object.")
    return body


def _many(params, name: str) -> tuple[str, ...]:
    values = [value.strip()[:MAX_VALUE_LENGTH] for value in params.getlist(name) if value.strip()]
    return tuple(values[:MAX_VALUES])


def _number(params, name: str, cast):
    raw = params.get(name, "").strip()
    if not raw:
        return None
    try:
        return cast(raw)
    except ValueError:
        raise ApiError(400, f"Invalid value for {name}.") from None


def _tri_state(params, name: str) -> bool | None:
    return {"yes": True, "no": False}.get(params.get(name, ""))


def parse_filters(params) -> repository.Filters:
    return repository.Filters(
        q=params.get("q", "").strip()[:MAX_VALUE_LENGTH],
        positions=_many(params, "position"),
        position_terms=_many(params, "position_term"),
        departments=_many(params, "department"),
        department_terms=_many(params, "department_term"),
        exp_min=_number(params, "exp_min", float),
        exp_max=_number(params, "exp_max", float),
        score_min=_number(params, "score_min", int),
        received_from=_number(params, "received_from", date.fromisoformat),
        received_to=_number(params, "received_to", date.fromisoformat),
        hr_star=_tri_state(params, "hr_star"),
        hod_star=_tri_state(params, "hod_star"),
    )


def _public_user(user: dict) -> dict:
    return {"username": user["username"], "display_name": user["display_name"], "role": user["role"]}


def _admin_user(user: dict) -> dict:
    return {**_public_user(user), "active": user["active"], "created_at": user["created_at"].isoformat()}


def _role(raw: object) -> str:
    if raw not in repository.USER_ROLES:
        raise ValueError(f"Role must be one of: {', '.join(repository.USER_ROLES)}.")
    return raw


def create_app(settings: DashboardSettings) -> Starlette:
    schema = settings.database.schema
    pool = ConnectionPool(
        settings.database.conninfo(),
        min_size=1,
        max_size=8,
        open=False,
        kwargs={"row_factory": dict_row},
        name="dashboard",
    )
    limiter = auth.LoginLimiter()

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        pool.open(wait=True, timeout=15)
        repository.init_schema(pool, schema)
        logger.info("Dashboard ready (database %s, schema %s)", settings.database.name, schema)
        try:
            yield
        finally:
            pool.close()

    def authenticated(*roles: str):
        """Require a signed-in, active user. With `roles`, the user must also hold one of them.

        The user is re-read on every request, so disabling an account or changing its role takes effect at once.
        """

        def decorator(handler):
            @functools.wraps(handler)
            async def wrapper(request: Request) -> Response:
                username = request.session.get("user", "")
                user = await run_in_threadpool(repository.get_user, pool, schema, username) if username else None
                if not user or not user["active"]:
                    raise ApiError(401, "Please sign in.")
                if roles and user["role"] not in roles:
                    raise ApiError(403, "Your role cannot do this.")
                return await handler(request, user)

            return wrapper

        return decorator

    async def index(request: Request) -> Response:
        return FileResponse(STATIC_DIR / "index.html")

    async def health(request: Request) -> Response:
        return JSONResponse({"status": "ok"})

    async def login(request: Request) -> Response:
        _require_ajax(request)
        body = await _json_body(request)
        username = str(body.get("username", "")).strip().lower()
        password = str(body.get("password", ""))
        client = request.client.host if request.client else "-"
        throttle_key = f"{client}|{username}"
        if limiter.blocked(throttle_key):
            raise ApiError(429, "Too many failed attempts. Try again in a few minutes.")

        user = await run_in_threadpool(repository.get_user, pool, schema, username)
        usable = bool(user and user["active"])
        stored = user["password_hash"] if usable else auth.DUMMY_HASH
        valid = await run_in_threadpool(auth.verify_password, password, stored)
        if not (valid and usable):
            limiter.record_failure(throttle_key)
            logger.warning("Failed login for %r from %s", username, client)
            raise ApiError(401, "Invalid username or password.")

        limiter.reset(throttle_key)
        request.session.clear()
        request.session["user"] = user["username"]
        logger.info("Login: %s (%s)", user["username"], user["role"])
        return JSONResponse(_public_user(user))

    async def logout(request: Request) -> Response:
        _require_ajax(request)
        request.session.clear()
        return JSONResponse({"ok": True})

    @authenticated()
    async def me(request: Request, user: dict) -> Response:
        return JSONResponse(_public_user(user))

    @authenticated(*repository.STAR_ROLES)
    async def candidates(request: Request, user: dict) -> Response:
        params = request.query_params
        sort = params.get("sort", "received")
        if sort not in repository.SORT_COLUMNS:
            raise ApiError(400, "Unknown sort column.")
        direction = "asc" if params.get("dir") == "asc" else "desc"
        page_size = _number(params, "page_size", int) or repository.PAGE_SIZES[0]
        if page_size not in repository.PAGE_SIZES:
            raise ApiError(400, "Unsupported page size.")
        page = max(1, _number(params, "page", int) or 1)
        result = await run_in_threadpool(
            repository.search, pool, schema, parse_filters(params), sort, direction, page, page_size
        )
        return JSONResponse(result)

    @authenticated(*repository.STAR_ROLES)
    async def summary(request: Request, user: dict) -> Response:
        return JSONResponse(
            await run_in_threadpool(repository.summary, pool, schema, parse_filters(request.query_params))
        )

    @authenticated(*repository.STAR_ROLES)
    async def options(request: Request, user: dict) -> Response:
        field = request.path_params["field"]
        if field not in repository.OPTION_COLUMNS:
            raise ApiError(404, "Unknown field.")
        query = request.query_params.get("q", "").strip()[:MAX_VALUE_LENGTH]
        return JSONResponse(await run_in_threadpool(repository.options, pool, schema, field, query))

    @authenticated(*repository.STAR_ROLES)
    async def put_star(request: Request, user: dict) -> Response:
        _require_ajax(request)
        key = request.path_params["key"]
        if not CANDIDATE_KEY.fullmatch(key):
            raise ApiError(404, "Candidate not found.")
        body = await _json_body(request)
        starred = body.get("starred")
        if not isinstance(starred, bool):
            raise ApiError(400, "'starred' must be true or false.")
        found = await run_in_threadpool(
            repository.set_star, pool, schema, key, user["role"], user["username"], starred
        )
        if not found:
            raise ApiError(404, "Candidate not found.")
        action = "starred" if starred else "unstarred"
        logger.info("%s %s candidate %s as %s", user["username"], action, key, user["role"])
        return JSONResponse(await run_in_threadpool(repository.get_stars, pool, schema, key))

    # ---- admin portal ----

    @authenticated("admin")
    async def admin_list_users(request: Request, user: dict) -> Response:
        users = await run_in_threadpool(repository.list_users, pool, schema)
        return JSONResponse([_admin_user(row) for row in users])

    @authenticated("admin")
    async def admin_create_user(request: Request, user: dict) -> Response:
        _require_ajax(request)
        body = await _json_body(request)
        try:
            username = auth.normalise_username(body.get("username"))
            display_name = auth.clean_display_name(body.get("display_name"))
            role = _role(body.get("role"))
            password = auth.check_password(body.get("password"))
        except ValueError as error:
            raise ApiError(400, str(error)) from None
        password_hash = await run_in_threadpool(auth.hash_password, password)
        created = await run_in_threadpool(
            repository.create_user, pool, schema, username, display_name, role, password_hash
        )
        if not created:
            raise ApiError(409, f"User '{username}' already exists.")
        logger.info("Admin %s created %s user %s", user["username"], role, username)
        created_user = await run_in_threadpool(repository.get_user, pool, schema, username)
        return JSONResponse(_admin_user(created_user), status_code=201)

    @authenticated("admin")
    async def admin_update_user(request: Request, user: dict) -> Response:
        _require_ajax(request)
        username = request.path_params["username"]
        if not auth.USERNAME.fullmatch(username) or not await run_in_threadpool(
            repository.get_user, pool, schema, username
        ):
            raise ApiError(404, "User not found.")
        body = await _json_body(request)
        changes: dict = {}
        try:
            if "display_name" in body:
                changes["display_name"] = auth.clean_display_name(body["display_name"])
            if "role" in body:
                changes["role"] = _role(body["role"])
            if "active" in body:
                if not isinstance(body["active"], bool):
                    raise ValueError("'active' must be true or false.")
                changes["active"] = body["active"]
            if "password" in body:
                password = auth.check_password(body["password"])
                changes["password_hash"] = await run_in_threadpool(auth.hash_password, password)
        except ValueError as error:
            raise ApiError(400, str(error)) from None
        if not changes:
            raise ApiError(400, "Nothing to change.")
        if username == user["username"] and (
            changes.get("role", user["role"]) != "admin" or changes.get("active") is False
        ):
            raise ApiError(400, "You cannot remove your own admin role or disable your own account.")
        await run_in_threadpool(repository.update_user, pool, schema, username, **changes)
        logger.info(
            "Admin %s updated user %s (%s)",
            user["username"],
            username,
            ", ".join("password reset" if key == "password_hash" else key for key in changes),
        )
        updated = await run_in_threadpool(repository.get_user, pool, schema, username)
        return JSONResponse(_admin_user(updated))

    async def api_error(request: Request, exc: ApiError) -> Response:
        return JSONResponse({"error": exc.message}, status_code=exc.status)

    return Starlette(
        routes=[
            Route("/", index),
            Route("/healthz", health),
            Route("/api/login", login, methods=["POST"]),
            Route("/api/logout", logout, methods=["POST"]),
            Route("/api/me", me),
            Route("/api/candidates", candidates),
            Route("/api/summary", summary),
            Route("/api/options/{field}", options),
            Route("/api/candidates/{key}/star", put_star, methods=["PUT"]),
            Route("/api/admin/users", admin_list_users, methods=["GET"]),
            Route("/api/admin/users", admin_create_user, methods=["POST"]),
            Route("/api/admin/users/{username}", admin_update_user, methods=["PATCH"]),
            Mount("/static", StaticFiles(directory=STATIC_DIR), name="static"),
        ],
        middleware=[
            Middleware(
                SessionMiddleware,
                secret_key=settings.secret_key,
                session_cookie="hr_dashboard_session",
                max_age=settings.session_hours * 3600,
                same_site="strict",
                https_only=settings.https_only,
            ),
            Middleware(SecurityHeadersMiddleware),
        ],
        exception_handlers={ApiError: api_error},
        lifespan=lifespan,
    )
