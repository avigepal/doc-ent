"""Signing in to the dashboard.

Two secrets, two jobs:

  LOGIN_PASSWORD  what a person types on the login screen. The server checks it (POST /auth/login) and
                  answers with a session token that expires.
  BEARER_TOKEN    for programs (curl, scripts) that call the API directly. It never reaches the browser.

The API accepts either as `Authorization: Bearer ...`. A session token is stateless: it carries its own
expiry and an HMAC signature, so nothing is stored. The signing key is made from both secrets, so
changing LOGIN_PASSWORD or BEARER_TOKEN signs everybody out.

When LOGIN_PASSWORD is not set, the login password is the BEARER_TOKEN, as it was before this existed.
"""

from __future__ import annotations

import hashlib
import hmac
import threading
import time
from collections import defaultdict, deque

SESSION_PREFIX = "s1"

# Wrong passwords allowed from one address in one window, before it is turned away for the rest of it.
MAX_FAILURES = 5
FAILURE_WINDOW_S = 60.0


def effective_password(login_password: str, bearer_token: str) -> str:
    return login_password or bearer_token


def same_secret(presented: str, expected: str) -> bool:
    """Equal strings, compared in constant time (no timing hint about how much matched)."""
    return hmac.compare_digest(presented.encode("utf-8"), expected.encode("utf-8"))


def _key(password: str, bearer_token: str) -> bytes:
    return hashlib.sha256(f"docent-session|{password}|{bearer_token}".encode("utf-8")).digest()


def make_session_token(password: str, bearer_token: str, lifetime_s: float, now: float | None = None) -> tuple[str, int]:
    """A signed token that is good for `lifetime_s` seconds, and the moment it expires (epoch seconds)."""
    expires = int((time.time() if now is None else now) + lifetime_s)
    body = f"{SESSION_PREFIX}.{expires}"
    signature = hmac.new(_key(password, bearer_token), body.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{body}.{signature}", expires


def valid_session_token(token: str, password: str, bearer_token: str, now: float | None = None) -> bool:
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != SESSION_PREFIX or not parts[1].isdigit():
        return False
    if int(parts[1]) <= (time.time() if now is None else now):
        return False
    expected = hmac.new(_key(password, bearer_token), f"{parts[0]}.{parts[1]}".encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(parts[2].encode("utf-8"), expected.encode("utf-8"))


class LoginThrottle:
    """Slows down guessing: after MAX_FAILURES wrong passwords from one address within FAILURE_WINDOW_S,
    further attempts (even a right one) are refused until the oldest failure is a window old.
    In memory, per process: it resets when the api restarts."""

    def __init__(self, max_failures: int = MAX_FAILURES, window_s: float = FAILURE_WINDOW_S) -> None:
        self.max_failures = max_failures
        self.window_s = window_s
        self._failures: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> deque[float]:
        recent = self._failures[key]
        while recent and now - recent[0] >= self.window_s:
            recent.popleft()
        if not recent:
            self._failures.pop(key, None)
            return deque()
        return recent

    def wait_seconds(self, key: str, now: float | None = None) -> int:
        """0 when this address may try; otherwise how many seconds until it may."""
        now = time.monotonic() if now is None else now
        with self._lock:
            recent = self._prune(key, now)
            if len(recent) < self.max_failures:
                return 0
            return max(1, int(self.window_s - (now - recent[0])) + 1)

    def failed(self, key: str, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        with self._lock:
            self._prune(key, now)
            self._failures[key].append(now)

    def succeeded(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)
