from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from ..cookies import CookieError, encrypt_cookies, validate_cookies
from ..db import get_session
from ..models import CookieProfile, User, utcnow
from .deps import require_user

router = APIRouter(prefix="/v1/cookies", tags=["cookies"])

MAX_PROFILES_PER_USER = 10


class ProfileCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    domain: str = Field(min_length=1, max_length=253)
    cookies: str = Field(min_length=10)


class ProfileRead(BaseModel):
    id: str
    name: str
    domain: str
    created_at: datetime
    updated_at: datetime


def _to_read(profile: CookieProfile) -> ProfileRead:
    return ProfileRead(
        id=profile.id,
        name=profile.name,
        domain=profile.domain,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )


@router.get("/profiles", response_model=list[ProfileRead])
def list_profiles(
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> list[ProfileRead]:
    profiles = session.exec(
        select(CookieProfile)
        .where(CookieProfile.user_id == user.id)
        .order_by(CookieProfile.created_at.desc())
    ).all()
    return [_to_read(p) for p in profiles]


@router.post("/profiles", response_model=ProfileRead, status_code=201)
def create_profile(
    payload: ProfileCreate,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> ProfileRead:
    count = session.exec(
        select(CookieProfile).where(CookieProfile.user_id == user.id)
    ).all()
    if len(count) >= MAX_PROFILES_PER_USER:
        raise HTTPException(
            status_code=400,
            detail=f"You can save at most {MAX_PROFILES_PER_USER} cookie profiles. Delete one first.",
        )
    try:
        cookies_enc = encrypt_cookies(validate_cookies(payload.cookies))
    except CookieError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    profile = CookieProfile(
        user_id=user.id,
        name=payload.name.strip(),
        domain=payload.domain.strip().lower(),
        cookies_enc=cookies_enc,
    )
    session.add(profile)
    session.commit()
    session.refresh(profile)
    return _to_read(profile)


@router.delete("/profiles/{profile_id}", status_code=204)
def delete_profile(
    profile_id: str,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> None:
    profile = session.get(CookieProfile, profile_id)
    if profile is None or profile.user_id != user.id:
        raise HTTPException(status_code=404, detail="Profile not found")
    session.delete(profile)
    session.commit()
