from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from ..db import init_db
from .agent import router as agent_router
from .auth import router as auth_router
from .jobs import router as jobs_router

app = FastAPI(title="Shorekeep API", version="0.4.0")
app.include_router(auth_router)
app.include_router(agent_router)
app.include_router(jobs_router)

init_db()

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
