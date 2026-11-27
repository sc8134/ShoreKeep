from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session

from .. import bandwidth as bw
from ..db import engine, get_session
from ..models import BandwidthSettings, utcnow
from .deps import require_user

router = APIRouter(prefix="/v1/bandwidth", tags=["bandwidth"])


class BandwidthRead(BaseModel):
    limit_kbps: float
    limit_mbps: float
    unlimited: bool


class BandwidthSet(BaseModel):
    limit_kbps: float = Field(ge=0, description="Speed limit in KB/s. 0 = unlimited.")


def _read() -> BandwidthRead:
    kbps = bw.get_global_limit_kbps()
    return BandwidthRead(
        limit_kbps=kbps,
        limit_mbps=round(kbps / 1024, 3),
        unlimited=kbps <= 0,
    )


def _load_persisted() -> None:
    """Load the persisted global limit into the in-memory bucket on startup."""
    with Session(engine) as session:
        row = session.get(BandwidthSettings, 1)
        if row and row.limit_kbps > 0:
            bw.set_global_limit(row.limit_kbps)


@router.get("", response_model=BandwidthRead)
def get_bandwidth(
    _user=Depends(require_user),
) -> BandwidthRead:
    return _read()


@router.post("", response_model=BandwidthRead)
def set_bandwidth(
    payload: BandwidthSet,
    _user=Depends(require_user),
    session: Session = Depends(get_session),
) -> BandwidthRead:
    bw.set_global_limit(payload.limit_kbps)

    # Persist the setting.
    row = session.get(BandwidthSettings, 1)
    if row is None:
        row = BandwidthSettings(id=1, limit_kbps=payload.limit_kbps)
    else:
        row.limit_kbps = payload.limit_kbps
        row.updated_at = utcnow()
    session.add(row)
    session.commit()

    return _read()
