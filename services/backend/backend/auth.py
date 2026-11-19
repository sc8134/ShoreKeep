from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
from datetime import timedelta

from sqlmodel import Session, delete

from .config import settings
from .models import AuthSession, User, utcnow

SESSION_COOKIE = "mp_session"

_PASSWORD_MIN = 8
_PASSWORD_MAX = 128
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_MAXMEM = 64 * 1024 * 1024
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AuthError(ValueError):
    pass


def normalize_email(email: str) -> str:
    return email.strip().lower()


def validate_email(email: str) -> None:
    if len(email) > 254 or not _EMAIL_RE.match(email):
        raise AuthError("Enter a valid email address")


def validate_password(password: str) -> None:
    if len(password) < _PASSWORD_MIN:
        raise AuthError(f"Password must be at least {_PASSWORD_MIN} characters")
    if len(password) > _PASSWORD_MAX:
        raise AuthError(f"Password must be at most {_PASSWORD_MAX} characters")


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        maxmem=_SCRYPT_MAXMEM,
        dklen=32,
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_b64, hash_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(hash_b64)
        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=base64.b64decode(salt_b64),
            n=int(n),
            r=int(r),
            p=int(p),
            maxmem=_SCRYPT_MAXMEM,
            dklen=32,
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest, expected)


_DUMMY_HASH = hash_password("dummy-password-for-timing")


def burn_password_check(password: str) -> None:
    """Equalize login timing when the account does not exist."""
    verify_password(password, _DUMMY_HASH)


def get_user_by_email(session: Session, email: str) -> User | None:
    from sqlmodel import select

    return session.exec(select(User).where(User.email == normalize_email(email))).first()


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(
    session: Session,
    user: User,
    client_ip: str | None = None,
    user_agent: str | None = None,
) -> str:
    token = secrets.token_urlsafe(32)
    now = utcnow()
    session.add(
        AuthSession(
            token_hash=_token_hash(token),
            user_id=user.id,
            created_at=now,
            expires_at=now + timedelta(days=settings.session_days),
            ip_hash=hash_ip(client_ip),
            user_agent=(user_agent or "")[:256] or None,
        )
    )
    session.commit()
    return token


def resolve_session(session: Session, token: str | None) -> User | None:
    if not token:
        return None
    record = session.get(AuthSession, _token_hash(token))
    if record is None:
        return None
    if record.expires_at < utcnow():
        session.delete(record)
        session.commit()
        return None
    user = session.get(User, record.user_id)
    if user is None or user.status != "active":
        return None
    return user


def revoke_session(session: Session, token: str | None) -> None:
    if not token:
        return
    session.exec(delete(AuthSession).where(AuthSession.token_hash == _token_hash(token)))
    session.commit()


def purge_expired_sessions(session: Session) -> int:
    result = session.exec(delete(AuthSession).where(AuthSession.expires_at < utcnow()))
    session.commit()
    return result.rowcount or 0


def hash_ip(client_ip: str | None) -> str | None:
    """Stable pseudonymous key for per-IP quotas; raw IPs are never stored."""
    if not client_ip:
        return None
    digest = hashlib.sha256(f"{settings.secret_key}:{client_ip}".encode("utf-8")).hexdigest()
    return digest[:32]
