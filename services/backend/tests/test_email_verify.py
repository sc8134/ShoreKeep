"""Phase 1: email verification and password reset."""
from __future__ import annotations

import pytest
from sqlmodel import Session, select

from backend.auth import generate_reset_token, generate_verify_token
from backend.db import engine
from backend.models import User


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _get_user(email: str) -> User | None:
    with Session(engine) as s:
        return s.exec(select(User).where(User.email == email)).first()


def _register(client, email="verify@example.com", password="password123"):
    r = client.post("/v1/auth/register", json={"email": email, "password": password})
    assert r.status_code == 201, r.text
    return r.json()


# ---------------------------------------------------------------------------
# email_verified flag
# ---------------------------------------------------------------------------

def test_new_user_not_verified(client):
    _register(client)
    user = _get_user("verify@example.com")
    assert user is not None
    assert user.email_verified is False


def test_register_response_contains_email_verified_false(client):
    data = _register(client)
    assert data["user"]["email_verified"] is False


def test_verify_token_sets_flag(client):
    _register(client)
    user = _get_user("verify@example.com")
    with Session(engine) as s:
        u = s.get(User, user.id)
        token = generate_verify_token(s, u)

    r = client.get(f"/v1/auth/verify?token={token}")
    assert r.status_code == 200
    assert r.json()["verified"] is True

    user = _get_user("verify@example.com")
    assert user.email_verified is True


def test_verify_token_clears_hash(client):
    _register(client)
    user = _get_user("verify@example.com")
    with Session(engine) as s:
        u = s.get(User, user.id)
        token = generate_verify_token(s, u)

    client.get(f"/v1/auth/verify?token={token}")
    user = _get_user("verify@example.com")
    assert user.email_verify_token_hash is None


def test_verify_invalid_token_returns_400(client):
    r = client.get("/v1/auth/verify?token=notavalidtoken")
    assert r.status_code == 400


def test_verify_expired_token_returns_400(client):
    from datetime import timedelta
    _register(client)
    user = _get_user("verify@example.com")
    with Session(engine) as s:
        u = s.get(User, user.id)
        token = generate_verify_token(s, u)
        # manually expire it
        u = s.get(User, user.id)
        from backend.models import utcnow
        u.email_verify_expires_at = utcnow() - timedelta(seconds=1)
        s.add(u)
        s.commit()

    r = client.get(f"/v1/auth/verify?token={token}")
    assert r.status_code == 400


def test_verify_token_cannot_be_reused(client):
    _register(client)
    user = _get_user("verify@example.com")
    with Session(engine) as s:
        u = s.get(User, user.id)
        token = generate_verify_token(s, u)

    client.get(f"/v1/auth/verify?token={token}")
    r2 = client.get(f"/v1/auth/verify?token={token}")
    assert r2.status_code == 400


# ---------------------------------------------------------------------------
# forgot password
# ---------------------------------------------------------------------------

def test_forgot_always_200_registered(client):
    _register(client)
    r = client.post("/v1/auth/forgot", json={"email": "verify@example.com"})
    assert r.status_code == 200


def test_forgot_always_200_unknown(client):
    r = client.post("/v1/auth/forgot", json={"email": "nobody@example.com"})
    assert r.status_code == 200


def test_forgot_sets_reset_token(client):
    _register(client)
    client.post("/v1/auth/forgot", json={"email": "verify@example.com"})
    user = _get_user("verify@example.com")
    assert user.reset_token_hash is not None
    assert user.reset_token_expires_at is not None


# ---------------------------------------------------------------------------
# reset password
# ---------------------------------------------------------------------------

def test_reset_password_works(client):
    _register(client)
    user = _get_user("verify@example.com")
    with Session(engine) as s:
        u = s.get(User, user.id)
        token = generate_reset_token(s, u)

    r = client.post("/v1/auth/reset-password", json={"token": token, "password": "newpass999"})
    assert r.status_code == 200

    # old password no longer works
    r2 = client.post("/v1/auth/login", json={"email": "verify@example.com", "password": "password123"})
    assert r2.status_code == 401

    # new password works
    r3 = client.post("/v1/auth/login", json={"email": "verify@example.com", "password": "newpass999"})
    assert r3.status_code == 200


def test_reset_invalid_token_returns_400(client):
    r = client.post("/v1/auth/reset-password", json={"token": "badtoken", "password": "newpass999"})
    assert r.status_code == 400


def test_reset_expired_token_returns_400(client):
    from datetime import timedelta
    _register(client)
    user = _get_user("verify@example.com")
    with Session(engine) as s:
        u = s.get(User, user.id)
        token = generate_reset_token(s, u)
        u = s.get(User, user.id)
        from backend.models import utcnow
        u.reset_token_expires_at = utcnow() - timedelta(seconds=1)
        s.add(u)
        s.commit()

    r = client.post("/v1/auth/reset-password", json={"token": token, "password": "newpass999"})
    assert r.status_code == 400


def test_reset_token_cannot_be_reused(client):
    _register(client)
    user = _get_user("verify@example.com")
    with Session(engine) as s:
        u = s.get(User, user.id)
        token = generate_reset_token(s, u)

    client.post("/v1/auth/reset-password", json={"token": token, "password": "newpass999"})
    r2 = client.post("/v1/auth/reset-password", json={"token": token, "password": "anotherpass1"})
    assert r2.status_code == 400


def test_reset_password_too_short_returns_400(client):
    _register(client)
    user = _get_user("verify@example.com")
    with Session(engine) as s:
        u = s.get(User, user.id)
        token = generate_reset_token(s, u)

    r = client.post("/v1/auth/reset-password", json={"token": token, "password": "short"})
    assert r.status_code == 400
