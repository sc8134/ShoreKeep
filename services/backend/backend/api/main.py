from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .. import __version__
from ..db import init_db
from .agent import router as agent_router
from .auth import router as auth_router
from .billing import router as billing_router
from .jobs import router as jobs_router

app = FastAPI(title="Shorekeep API", version=__version__)
app.include_router(auth_router)
app.include_router(agent_router)
app.include_router(billing_router)
app.include_router(jobs_router)

init_db()

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
