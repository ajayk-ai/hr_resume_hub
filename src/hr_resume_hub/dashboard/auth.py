"""Password hashing and login throttling."""
import base64
import hashlib
import hmac
import os
import re
import threading
import time
from collections import defaultdict, deque

_N, _R, _P = 2**14, 8, 1  # scrypt cost parameters (about 16 MB, tens of milliseconds)

USERNAME = re.compile(r"[a-z0-9._-]{2,40}")
MIN_PASSWORD_LENGTH = 10
MAX_PASSWORD_LENGTH = 200  # bounds the scrypt work an attacker can request
MAX_DISPLAY_NAME_LENGTH = 80


def normalise_username(raw: object) -> str:
    username = raw.strip().lower() if isinstance(raw, str) else ""
    if not USERNAME.fullmatch(username):
        raise ValueError("Username must be 2-40 characters: lowercase letters, digits, '.', '_' or '-'.")
    return username


def clean_display_name(raw: object) -> str:
    name = " ".join(raw.split()) if isinstance(raw, str) else ""
    if not name or len(name) > MAX_DISPLAY_NAME_LENGTH:
        raise ValueError(f"Display name is required (up to {MAX_DISPLAY_NAME_LENGTH} characters).")
    return name


def check_password(raw: object) -> str:
    if not isinstance(raw, str) or not MIN_PASSWORD_LENGTH <= len(raw) <= MAX_PASSWORD_LENGTH:
        raise ValueError(f"Password must be {MIN_PASSWORD_LENGTH} to {MAX_PASSWORD_LENGTH} characters.")
    return raw


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return "$".join(["scrypt", str(_N), str(_R), str(_P), _b64(salt), _b64(digest)])


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(digest)
        actual = hashlib.scrypt(
            password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), dklen=len(expected)
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


# Verified against when the username is unknown, so response time does not reveal valid usernames.
DUMMY_HASH = hash_password("not-a-real-password")


class LoginLimiter:
    """Blocks a key (client address + username) after repeated failed logins."""

    def __init__(self, max_failures: int = 5, window_seconds: int = 900) -> None:
        self._max = max_failures
        self._window = window_seconds
        self._failures: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _recent(self, key: str) -> deque[float]:
        failures = self._failures[key]
        cutoff = time.monotonic() - self._window
        while failures and failures[0] < cutoff:
            failures.popleft()
        return failures

    def blocked(self, key: str) -> bool:
        with self._lock:
            return len(self._recent(key)) >= self._max

    def record_failure(self, key: str) -> None:
        with self._lock:
            self._recent(key).append(time.monotonic())

    def reset(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)
