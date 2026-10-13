import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from unittest.mock import MagicMock

from app import auth, main
from app.config import settings
from app.db import get_session

PASSWORD, BEARER = "correct horse", "api-token-123"


# ---------- session tokens ----------


def test_a_session_token_is_valid_until_it_expires():
    token, expires = auth.make_session_token(PASSWORD, BEARER, lifetime_s=3600, now=1_000_000)

    assert expires == 1_003_600
    assert auth.valid_session_token(token, PASSWORD, BEARER, now=1_000_001)
    assert auth.valid_session_token(token, PASSWORD, BEARER, now=1_003_599)
    assert not auth.valid_session_token(token, PASSWORD, BEARER, now=1_003_600)  # the moment it expires
    assert not auth.valid_session_token(token, PASSWORD, BEARER, now=2_000_000)


def test_a_token_made_with_other_secrets_or_edited_is_refused():
    token, _ = auth.make_session_token(PASSWORD, BEARER, 3600, now=1_000_000)
    prefix, expires, signature = token.split(".")

    assert not auth.valid_session_token(token, "another password", BEARER, now=1_000_001)  # new password signs everybody out
    assert not auth.valid_session_token(token, PASSWORD, "another token", now=1_000_001)
    assert not auth.valid_session_token(f"{prefix}.{int(expires) + 999_999}.{signature}", PASSWORD, BEARER, now=1_000_001)
    assert not auth.valid_session_token(f"{prefix}.{expires}.{'0' * len(signature)}", PASSWORD, BEARER, now=1_000_001)


@pytest.mark.parametrize("junk", ["", "abc", "s1.123", "s1.abc.def", "s2.9999999999.abc", "a.b.c.d", "Bearer x", "s1..", "s1.-5.x"])
def test_malformed_tokens_are_refused_not_crashed_on(junk):
    assert not auth.valid_session_token(junk, PASSWORD, BEARER, now=1_000_000)


def test_the_bearer_token_is_not_a_session_token():
    assert not auth.valid_session_token(BEARER, PASSWORD, BEARER)


def test_the_password_defaults_to_the_bearer_token_only_when_none_is_set():
    assert auth.effective_password("", BEARER) == BEARER
    assert auth.effective_password(PASSWORD, BEARER) == PASSWORD


def test_secrets_compare_equal_only_when_identical_including_non_ascii():
    assert auth.same_secret("pässwörd", "pässwörd")
    assert not auth.same_secret("pässwörd", "passwörd")
    assert not auth.same_secret("a", "") and not auth.same_secret("", "a")


# ---------- slowing down guessing ----------


def test_five_wrong_passwords_lock_an_address_out_until_the_window_passes():
    throttle = auth.LoginThrottle(max_failures=5, window_s=60)
    for second in range(4):
        throttle.failed("1.2.3.4", now=second)
    assert throttle.wait_seconds("1.2.3.4", now=4) == 0  # four is still allowed
    throttle.failed("1.2.3.4", now=4)

    wait = throttle.wait_seconds("1.2.3.4", now=10)
    assert 50 <= wait <= 55  # until the oldest failure is a minute old
    assert throttle.wait_seconds("1.2.3.4", now=61) < wait  # it is counting down
    assert throttle.wait_seconds("1.2.3.4", now=60 + 4 + 1) == 0  # all five are a window old


def test_one_address_being_locked_out_does_not_lock_out_another():
    throttle = auth.LoginThrottle(max_failures=2, window_s=60)
    throttle.failed("a", now=0)
    throttle.failed("a", now=1)
    assert throttle.wait_seconds("a", now=2) > 0 and throttle.wait_seconds("b", now=2) == 0


def test_a_right_password_clears_the_failures():
    throttle = auth.LoginThrottle(max_failures=3, window_s=60)
    throttle.failed("a", now=0)
    throttle.failed("a", now=1)
    throttle.succeeded("a")
    throttle.failed("a", now=2)
    throttle.failed("a", now=3)
    assert throttle.wait_seconds("a", now=4) == 0


# ---------- what the API accepts ----------


@pytest.fixture
def secrets(monkeypatch):
    monkeypatch.setattr(settings, "login_password", PASSWORD)
    monkeypatch.setattr(settings, "bearer_token", BEARER)
    monkeypatch.setattr(settings, "session_hours", 1)


def _passes(header):
    try:
        main.require_bearer_token(header)
        return True
    except HTTPException as exc:
        assert exc.status_code == 401
        return False


def test_the_api_accepts_the_bearer_token_and_a_live_session_but_nothing_else(secrets):
    session, _ = auth.make_session_token(PASSWORD, BEARER, 3600)

    assert _passes(f"Bearer {BEARER}")
    assert _passes(f"Bearer {session}")
    assert not _passes(None)
    assert not _passes("")
    assert not _passes("Bearer ")
    assert not _passes(BEARER)  # no "Bearer " prefix
    assert not _passes(f"bearer {BEARER}")
    assert not _passes(f"Bearer {PASSWORD}")  # the login password is not an API credential
    assert not _passes("Bearer s1.9999999999.deadbeef")


def test_an_expired_session_is_refused(secrets):
    old, _ = auth.make_session_token(PASSWORD, BEARER, lifetime_s=-5)
    assert not _passes(f"Bearer {old}")


def test_changing_the_password_signs_everybody_out(secrets, monkeypatch):
    session, _ = auth.make_session_token(PASSWORD, BEARER, 3600)
    assert _passes(f"Bearer {session}")
    monkeypatch.setattr(settings, "login_password", "a new password")
    assert not _passes(f"Bearer {session}")
    assert _passes(f"Bearer {BEARER}")  # programs are unaffected


# ---------- the login endpoint ----------


@pytest.fixture
def client(secrets, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda seconds: None)  # the small cost of a wrong guess
    monkeypatch.setattr(main, "_login_throttle", auth.LoginThrottle(max_failures=5, window_s=60))
    main.app.dependency_overrides[get_session] = lambda: MagicMock()
    yield TestClient(main.app)
    main.app.dependency_overrides.clear()


def _login(client, password):
    return client.post("/auth/login", json={"password": password})


def _authed_status(client, token):
    """400 means the request got past authentication (it is missing its content); 401 means it did not."""
    return client.post("/export", json={"filename": "x"}, headers={"Authorization": f"Bearer {token}"}).status_code


def test_the_right_password_returns_a_session_the_api_accepts(client):
    response = _login(client, PASSWORD)

    assert response.status_code == 200
    body = response.json()
    assert body["token"].startswith("s1.") and body["expires_at"] > 0
    assert PASSWORD not in body["token"] and BEARER not in body["token"]  # the secrets are never sent back
    assert _authed_status(client, body["token"]) == 400
    assert _authed_status(client, "nonsense") == 401


def test_a_wrong_or_empty_password_is_refused_at_the_login_screen(client):
    for wrong in ("nope", "", " ", PASSWORD.upper(), PASSWORD + " "):
        response = _login(client, wrong)
        assert response.status_code == 401 and response.json()["detail"] == "Wrong password"


def test_the_bearer_token_does_not_work_as_the_dashboard_password_once_a_login_password_is_set(client):
    assert _login(client, BEARER).status_code == 401


def test_without_a_login_password_the_bearer_token_is_the_password(client, monkeypatch):
    monkeypatch.setattr(settings, "login_password", "")
    response = _login(client, BEARER)

    assert response.status_code == 200
    assert _authed_status(client, response.json()["token"]) == 400


def test_repeated_wrong_passwords_are_turned_away_even_for_the_right_one(client):
    for _ in range(5):
        assert _login(client, "wrong").status_code == 401

    blocked = _login(client, PASSWORD)
    assert blocked.status_code == 429
    assert int(blocked.headers["retry-after"]) > 0 and "Try again" in blocked.json()["detail"]


def test_a_session_signs_out_when_the_login_password_changes(client, monkeypatch):
    token = _login(client, PASSWORD).json()["token"]
    assert _authed_status(client, token) == 400
    monkeypatch.setattr(settings, "login_password", "something else")
    assert _authed_status(client, token) == 401


def test_the_login_endpoint_itself_needs_no_credentials_but_everything_else_does(client):
    assert _login(client, "x").status_code == 401  # reached the password check, not refused for a missing header
    assert client.get("/health").status_code == 200
    assert client.post("/export", json={"filename": "x"}).status_code == 401
