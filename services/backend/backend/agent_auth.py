from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta

from sqlmodel import Session, delete, select

from .config import settings
from .models import AgentDevice, AgentPair, User, utcnow


def hash_agent_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def purge_expired_pairs(session: Session) -> int:
    result = session.exec(delete(AgentPair).where(AgentPair.expires_at < utcnow()))
    session.commit()
    return result.rowcount or 0


def create_pair(session: Session, user: User) -> tuple[str, AgentPair]:
    purge_expired_pairs(session)
    token = secrets.token_urlsafe(32)
    now = utcnow()
    pair = AgentPair(
        token_hash=hash_agent_token(token),
        user_id=user.id,
        created_at=now,
        expires_at=now + timedelta(seconds=settings.agent_pair_ttl_seconds),
    )
    session.add(pair)
    session.commit()
    return token, pair


def claim_pair(session: Session, token: str, label: str | None) -> tuple[str, AgentDevice, User] | None:
    """Consume a one-time pair token and issue a device token. Returns None if invalid."""
    pair = session.get(AgentPair, hash_agent_token(token))
    if pair is None:
        return None
    if pair.expires_at < utcnow():
        session.delete(pair)
        session.commit()
        return None
    user = session.get(User, pair.user_id)
    if user is None or user.status != "active":
        session.delete(pair)
        session.commit()
        return None

    session.delete(pair)
    device_token = secrets.token_urlsafe(32)
    device = AgentDevice(
        token_hash=hash_agent_token(device_token),
        user_id=user.id,
        label=(label or "Desktop app").strip()[:64] or "Desktop app",
        last_seen_at=utcnow(),
    )
    session.add(device)
    session.commit()
    session.refresh(device)
    return device_token, device, user


def resolve_device(session: Session, token: str | None) -> tuple[AgentDevice, User] | None:
    if not token:
        return None
    device = session.exec(
        select(AgentDevice).where(AgentDevice.token_hash == hash_agent_token(token))
    ).first()
    if device is None:
        return None
    if device.last_seen_at is None or (utcnow() - device.last_seen_at).total_seconds() > 300:
        device.last_seen_at = utcnow()
        session.add(device)
        session.commit()
    user = session.get(User, device.user_id)
    if user is None or user.status != "active":
        return None
    return device, user
