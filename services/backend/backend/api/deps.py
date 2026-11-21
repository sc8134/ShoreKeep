from __future__ import annotations

from urllib.parse import urlparse

from fastapi import Depends, HTTPException, Request
from sqlmodel import Session

from ..auth import SESSION_COOKIE, resolve_session
from ..billing import sync_plan
from ..config import settings
from ..db import get_session
from ..models import User


def _read_token(request: Request) -> tuple[str | None, str | None]:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip() or None, "bearer"
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        return token, "cookie"
    return None, None


def optional_user(
    request: Request,
    session: Session = Depends(get_session),
) -> User | None:
    token, source = _read_token(request)
    request.state.session_token = token
    request.state.auth_source = None
    user = resolve_session(session, token)
    if user is not None:
        request.state.auth_source = source
        user = sync_plan(session, user)
    return user


def require_user(user: User | None = Depends(optional_user)) -> User:
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="Sign in to continue",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def ensure_same_origin(request: Request) -> None:
    """CSRF guard for cookie-authenticated mutations (Bearer tokens are exempt)."""
    if getattr(request.state, "auth_source", None) != "cookie":
        return
    origin = request.headers.get("origin")
    if not origin:
        return
    host = urlparse(origin).netloc.lower()
    allowed = {
        urlparse(settings.public_base_url).netloc.lower(),
        request.url.netloc.lower(),
    }
    if host not in allowed:
        raise HTTPException(status_code=403, detail="Cross-origin request blocked")


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"
