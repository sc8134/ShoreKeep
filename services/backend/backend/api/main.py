from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .. import __version__
from ..config import settings
from ..db import init_db
from .agent import router as agent_router
from .auth import router as auth_router
from .bandwidth import router as bandwidth_router
from .billing import router as billing_router
from .cookie_profiles import router as cookie_profiles_router
from .jobs import router as jobs_router

_DEV_KEY = "dev-insecure-change-me"


def _check_production_config() -> None:
    """Hard-fail on the most dangerous misconfiguration: a known-insecure secret
    key in an environment that looks like production. Cookies encrypted with the
    dev key are trivially decryptable by anyone who reads the source.
    """
    if settings.secret_key == _DEV_KEY:
        if settings.cookie_secure or (settings.database_url or "").strip():
            raise RuntimeError(
                "SECRET_KEY is set to the insecure development default. "
                "Generate a strong random value and set it in your environment "
                "before starting the server. "
                "Example: python -c \"import secrets; print(secrets.token_hex(32))\""
            )


app = FastAPI(title="Shorekeep API", version=__version__)
app.include_router(auth_router)
app.include_router(agent_router)
app.include_router(bandwidth_router)
app.include_router(billing_router)
app.include_router(cookie_profiles_router)
app.include_router(jobs_router)

_check_production_config()
init_db()

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
