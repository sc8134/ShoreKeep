from datetime import timedelta

from sqlmodel import Session

from backend.auth import (
    AuthError,
    create_session,
    hash_password,
    normalize_email,
    resolve_session,
    revoke_session,
    validate_email,
    validate_password,
    verify_password,
)
from backend.db import engine
from backend.models import AuthSession, User, utcnow


def test_password_hash_roundtrip():
    stored = hash_password("correct horse battery")
    assert stored.startswith("scrypt$")
    assert verify_password("correct horse battery", stored) is True
    assert verify_password("wrong password", stored) is False


def test_hash_is_salted():
    assert hash_password("same-password") != hash_password("same-password")


def test_password_policy():
    for bad in ("short", "x" * 129):
        try:
            validate_password(bad)
        except AuthError:
            continue
        raise AssertionError(f"password {bad!r} should be rejected")


def test_email_validation_and_normalization():
    assert normalize_email("  User@Example.COM ") == "user@example.com"
    validate_email("user@example.com")
    for bad in ("nope", "a@b", "a b@c.d", "@example.com"):
        try:
            validate_email(bad)
        except AuthError:
            continue
        raise AssertionError(f"email {bad!r} should be rejected")


def _make_user(session: Session, email: str = "owner@example.com") -> User:
    user = User(email=email, password_hash=hash_password("password123"))
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def test_session_lifecycle():
    with Session(engine) as session:
        user = _make_user(session)
        token = create_session(session, user, "127.0.0.1", "pytest")
        assert resolve_session(session, token).id == user.id
        revoke_session(session, token)
        assert resolve_session(session, token) is None


def test_expired_session_is_rejected_and_removed():
    with Session(engine) as session:
        user = _make_user(session)
        token = create_session(session, user)
        record = session.get(AuthSession, __import__("hashlib").sha256(token.encode()).hexdigest())
        record.expires_at = utcnow() - timedelta(seconds=5)
        session.add(record)
        session.commit()

        assert resolve_session(session, token) is None
        assert session.get(AuthSession, record.token_hash) is None
